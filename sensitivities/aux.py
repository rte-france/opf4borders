"""
    Copyright 2025 RTE (http://www.rte-france.com)

    Licensed under the Apache License, Version 2.0 (the "License");
    you may not use this file except in compliance with the License.
    You may obtain a copy of the License at

        http://www.apache.org/licenses/LICENSE-2.0

    Unless required by applicable law or agreed to in writing, software
    distributed under the License is distributed on an "AS IS" BASIS,
    WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
    See the License for the specific language governing permissions and
    limitations under the License.
"""

import pypowsybl.network as nt
import pypowsybl.loadflow as lf
import pypowsybl.sensitivity as ss
from pypowsybl._pypowsybl import PyPowsyblError
import pandas as pd
import numpy as np

# Threshold
# if abs(reactance) < threshold, then reactance = MIN_IMPEDANCE * sign(reactance)
MIN_IMPEDANCE = 1E-5

# Threshold
# if abs(sensi) is < threshold, then it is considered to be 0
SENSI_THRESHOLD = 1E-6


def adjust_network(network: nt.Network) -> nt.Network:
    """
        Updates network to be coherent with julia's formulation
    """

    # Remove losses for HVDCs (hvdc line resistance)
    hvdc_lines = network.get_hvdc_lines()
    if len(hvdc_lines) != 0:
        updated_hvdc_lines = pd.DataFrame(
            index=hvdc_lines.index.to_list(),
            columns=["r"],
            data=len(hvdc_lines) * [0.0]
        )
        network.update_hvdc_lines(updated_hvdc_lines)

    # Remove losses for HVDCs (converter station losses and voltage control)
    vsc_converter_stations = network.get_vsc_converter_stations()
    if len(vsc_converter_stations) != 0:
        updated_loss_factor_voltage_regulator_on = []
        for i in range(len(vsc_converter_stations)):
            updated_loss_factor_voltage_regulator_on.append([0.0, False])
        updated_vsc_converter_stations = pd.DataFrame(
            index=vsc_converter_stations.index.to_list(),
            columns=["loss_factor", "voltage_regulator_on"],
            data=updated_loss_factor_voltage_regulator_on
        )
        network.update_vsc_converter_stations(updated_vsc_converter_stations)

    # Disconnect batteries
    batteries = network.get_batteries()
    if len(batteries) != 0:
        updated_batteries = pd.DataFrame(
            index=batteries.index.to_list(),
            columns=["connected"],
            data=len(batteries) * [False]
        )
        network.update_batteries(updated_batteries)

    # Update branches (lines/t2wt) after setting a new min impedance
    def max_impedance_if_needed(x):
        if abs(x) < MIN_IMPEDANCE:
            if x >= 0:
                return MIN_IMPEDANCE
            return -MIN_IMPEDANCE
        return x

    lines = network.get_lines(attributes=["x"])
    lines["x"] = lines["x"].apply(max_impedance_if_needed)
    network.update_lines(lines)

    t2wts = network.get_2_windings_transformers(attributes=["x"])
    t2wts["x"] = t2wts["x"].apply(max_impedance_if_needed)
    network.update_2_windings_transformers(t2wts)

    return network


def hvdc_lines_full_setpoint(network: nt.Network, active_hvdc_lines_ids: list):
    """Disables droop from all active HVDC lines"""
    hvdc_angle_droop = network.get_extensions("hvdcAngleDroopActivePowerControl")
    hvdc_lines_ids = set(hvdc_angle_droop[hvdc_angle_droop["enabled"]].index)
    hvdc_lines_ids.intersection_update(set(active_hvdc_lines_ids))
    updated_hvdc_angle_droop = pd.DataFrame(
        index=list(hvdc_lines_ids),
        columns=["enabled"],
        data=len(hvdc_lines_ids) * [False]
    )
    network.update_extensions("hvdcAngleDroopActivePowerControl", updated_hvdc_angle_droop)


def enhance_border_hvdc_dataframe(network:nt.Network):
    """Add pertinent information to the hvdc dataframe and returns it.
    
    Adding following columns:
        - voltage_level_id_or (ie side 1)
        - voltage_level_id_end
        - bus_id_or
        - bus_id_end
        - nominal_v_or (of the voltage level)
        - nominal_v_end
        - substation_id_or
        - substation_id_end
        - country_or
        - country_end
        - p_or
        - q_or
        - p_end
        - q_end
        - 
    """
    hvdcs = network.get_hvdc_lines()

    vlvs = network.get_voltage_levels(attributes=["substation_id", "nominal_v"])
    subs = network.get_substations(attributes=["country"])
    vlvs = vlvs.join(subs, on="substation_id")

    vscs = network.get_vsc_converter_stations(attributes=["voltage_level_id", "bus_id", "p", "q"])
    vscs = vscs.join(vlvs, on="voltage_level_id")

    hvdcs = hvdcs[hvdcs["connected1"] & hvdcs["connected2"]]
    hvdcs = hvdcs.join(vscs, on="converter_station1_id", lsuffix="_hvdc")
    hvdcs = hvdcs.join(vscs, on="converter_station2_id", lsuffix="_or", rsuffix="_end")
    return hvdcs


def add_exchange_sign_to_hvdc_df(network:nt.Network, country1:str, country2:str):
    """ Add a column with the exchange sign of an HVDC
     The positivity is defined for a flow going effectively from country1 to country2
     The new column value is:
        * 1 for flow going effectively from country 1 to country 2
        * -1 for flow going in the opposite direction"""
    hvdcs = enhance_border_hvdc_dataframe(network)
    hvdcs = hvdcs[(hvdcs["country_or"].isin([country1, country2])) & (hvdcs["country_end"].isin([country1, country2]))]
    hvdcs_border = hvdcs[hvdcs["country_or"] != hvdcs["country_end"]].copy()
    hvdcs_border["exchange_sign"] = 0
    # Postivity of this column is defined for an export from country1 to country2
    hvdcs_border.loc[(hvdcs_border["country_or"] == country1) & \
                     (hvdcs_border["country_end"] == country2), "exchange_sign"] = 1
    hvdcs_border.loc[(hvdcs_border["country_or"] == country2) & \
                     (hvdcs_border["country_end"] == country1), "exchange_sign"] = -1
    # But positive flow is in the direction station rectifier -> station inverter
    # if needed, we have to change the sign
    hvdcs_border.loc[hvdcs_border["converters_mode"] == "SIDE_1_INVERTER_SIDE_2_RECTIFIER", "exchange_sign"] *= -1

    # print(hvdcs_border)
    return hvdcs_border


def get_border_countries(network: nt.Network, active_hvdc_lines_ids: list):
    """Returns the country linked by the HVDC lines in the active_hvdc_lines_ids list"""
    hvdcs = enhance_border_hvdc_dataframe(network)
    hvdcs = hvdcs.loc[active_hvdc_lines_ids]

    return hvdcs[["country_or", "country_end"]].iloc[0].to_list()


def create_ac_lines_to_simulate_hvdc_ac_emulation(network: nt.Network, active_hvdc_lines_ids: list):
    """"Creates an AC line of impedance 1/k parallel to the active HVDC lines that are in AC 
    emulation. Set the target_p of the lines at the same level than tht p0 of the AC emulation"""
    hvdc_lines = enhance_border_hvdc_dataframe(network)
    # gives a dataframe with columns :
    # "converters_mode" "converter_station1_id" "converter_station2_id" "voltage_level_id1"
    # "bus_id1" "voltage_level_id2" "bus_id2" "nominal_v1" "nominal_v2"

    bus_breaker_view = network.get_bus_breaker_view_buses()
    busbar = network.get_busbar_sections()

    # Convert HVDC lines to AC lines
    hvdc_angle_droop = network.get_extensions("hvdcAngleDroopActivePowerControl")
    hvdc_lines_ids = set(hvdc_angle_droop[hvdc_angle_droop["enabled"]].index)

    # Filter for active hvdc lines
    hvdc_lines_ids = list(hvdc_lines_ids.intersection(set(active_hvdc_lines_ids)))

    if len(hvdc_lines_ids) == 0:
        return []

    for hvdc_line_id in hvdc_lines_ids:
        hvdc_line = hvdc_lines.loc[hvdc_line_id]

        # Hvdc positive flow is from RECTIFIER to INVERTER
        origin_suffix = "_or" if hvdc_line.converters_mode == "SIDE_1_RECTIFIER_SIDE_2_INVERTER" else "_end"
        end_suffix = "_end" if hvdc_line.converters_mode == "SIDE_1_RECTIFIER_SIDE_2_INVERTER" else "_or"

        ac_eq_line_id = "ac_eq_line_" + hvdc_line_id

        voltage_level_origin = hvdc_line["voltage_level_id" + origin_suffix]
        nominal_v_origin = hvdc_line["nominal_v" + origin_suffix]
        bus_breaker_origin = hvdc_line["bus_id" + origin_suffix]
        buses_ids_origin = bus_breaker_view[bus_breaker_view["voltage_level_id"] == voltage_level_origin].index.to_list()
        bus_origin = None
        for i, bus_id in enumerate(buses_ids_origin):
            bus_breaker_id = f"{voltage_level_origin}_{i}"
            if bus_breaker_id == bus_breaker_origin:
                bus_origin = bus_id
                break

        voltage_level_end = hvdc_line["voltage_level_id" + end_suffix]
        nominal_v_end = hvdc_line["nominal_v" + end_suffix]
        bus_breaker_end = hvdc_line["bus_id" + end_suffix]
        buses_ids_end = bus_breaker_view[bus_breaker_view["voltage_level_id"] == voltage_level_end].index.to_list()
        bus_end = None
        for i, bus_id in enumerate(buses_ids_end):
            bus_breaker_id = f"{voltage_level_end}_{i}"
            if bus_breaker_id == bus_breaker_end:
                bus_end = bus_id
                break

        # Droop is given in MW/deg
        #   Convert to MW/rad
        #   Then to pu/rad
        droop = hvdc_angle_droop.loc[hvdc_line_id].droop
        droop *= 180 / np.pi
        droop /= 100
        line_reactance = 1 / droop
        line_reactance *= nominal_v_origin * nominal_v_end / 100
        try:
            network.create_lines(id=ac_eq_line_id, r=0.0, x=line_reactance,
                                 voltage_level1_id=voltage_level_origin, bus1_id=bus_origin,
                                 connectable_bus1_id=bus_origin, voltage_level2_id=voltage_level_end,
                                 bus2_id=bus_end, connectable_bus2_id=bus_end)
        except PyPowsyblError: # Node breaker network
            busbar_origin = busbar[busbar["voltage_level_id"] == voltage_level_origin]
            busbar_end = busbar[busbar["voltage_level_id"] == voltage_level_end]
            nt.create_line_bays(network, id=ac_eq_line_id,  r=0.0, x=line_reactance,
                                bus_or_busbar_section_id_1=busbar_origin.index[0], position_order_1=1,
                                bus_or_busbar_section_id_2=busbar_end.index[0], position_order_2=1)

    updated_p0 = hvdc_angle_droop.loc[hvdc_lines_ids]["p0"].values / 100  # Convert to pu
    updated_hvdc_lines = pd.DataFrame(
        index=hvdc_lines_ids,
        columns=["target_p"],
        data=updated_p0
    )
    network.update_hvdc_lines(updated_hvdc_lines)
    return hvdc_lines_ids


def add_generators_at_hvdcs_extremities(network: nt.Network, hvdc_list: list):
    """Adds generators at the ends of the HVDCs and returns a dataframe
    mapping each hvdc to the created generators"""
    hvdc_df = network.get_hvdc_lines(attributes=['converter_station1_id', 'converter_station2_id', 'target_p', 'converters_mode'])
    vsc_df = network.get_vsc_converter_stations(attributes=["bus_id", "bus_breaker_bus_id", "voltage_level_id", "node", "target_q"])
    hvdc_df = hvdc_df.join(vsc_df, on="converter_station1_id")
    hvdc_df = hvdc_df.join(vsc_df, on="converter_station2_id", lsuffix="1", rsuffix="2")
    # print(hvdc_df)

    hvdc_to_fictitious_gen = {}
    created_gens = []
    for hvdc_line in hvdc_list:
        hvdc_to_fictitious_gen[hvdc_line] = {
            "origin":f'{hvdc_df.loc[hvdc_line, "voltage_level_id1"]}_fict_hvdc_gen',
            "end":f'{hvdc_df.loc[hvdc_line, "voltage_level_id2"]}_fict_hvdc_gen'
            }
        created_gens.append(hvdc_to_fictitious_gen[hvdc_line]["origin"])
        try:
            network.create_generators(id=hvdc_to_fictitious_gen[hvdc_line]["origin"],
                                      voltage_level_id=hvdc_df.loc[hvdc_line, "voltage_level_id1"],
                                      bus_id=hvdc_df.loc[hvdc_line, "bus_breaker_bus_id1"],
                                      target_p=0, min_p=-1000,
                                      max_p=1000, target_q=0,
                                      voltage_regulator_on=False)
            network.create_generators(id=hvdc_to_fictitious_gen[hvdc_line]["end"],
                                      voltage_level_id=hvdc_df.loc[hvdc_line, "voltage_level_id2"],
                                      bus_id=hvdc_df.loc[hvdc_line, "bus_breaker_bus_id2"],
                                      target_p=0, min_p=-1000,
                                      max_p=1000, target_q=0,
                                      voltage_regulator_on=False)
        except PyPowsyblError:
            # Node breaker
            busbar = network.get_busbar_sections()#.loc[slack_vl_id]
            busbar_gen1 = busbar[busbar["voltage_level_id"] == hvdc_df.loc[hvdc_line, "voltage_level_id1"]]
            busbar_gen2 = busbar[busbar["voltage_level_id"] == hvdc_df.loc[hvdc_line, "voltage_level_id2"]]
            nt.create_generator_bay(network, id=hvdc_to_fictitious_gen[hvdc_line]["origin"],
                                            max_p=100, min_p=0, voltage_regulator_on=False,
                                            target_p=0, target_q=0,
                                            bus_or_busbar_section_id=busbar_gen1.index[0],
                                            position_order=1)
            nt.create_generator_bay(network, id=hvdc_to_fictitious_gen[hvdc_line]["end"],
                                            max_p=100, min_p=0, voltage_regulator_on=False,
                                            target_p=0, target_q=0,
                                            bus_or_busbar_section_id=busbar_gen2.index[0],
                                            position_order=1)

    hvdc_to_fictitious_gen_df = pd.DataFrame.from_dict(hvdc_to_fictitious_gen,orient="index")
    # print(hvdc_to_fictitious_gen_df)
    return hvdc_to_fictitious_gen_df


def calculate_exchange(network:nt.Network, hvdcs_border:pd.DataFrame, country1:str, country2:str):
    """Calculate exchange country1 -> country2, considering that the border HVDC are operated in
    setpoint mode. The exchange is calculated with power values on country1 side"""
    vlvs = network.get_voltage_levels(attributes=["substation_id"])
    subs = network.get_substations(attributes=["country"])
    vlvs = vlvs.join(subs, on="substation_id")

    branches = network.get_branches(attributes=["voltage_level1_id", "voltage_level2_id", "p1", "p2"])
    branches = branches.join(vlvs, on="voltage_level1_id")
    branches = branches.join(vlvs, on="voltage_level2_id", lsuffix="_or", rsuffix="_end")

    # it may occur that an XNODE is in one country and the end of its only incoming branch is in the other,
    # in this case, this line should not be considered as a border interconnection
    false_border = []
    for line in branches[(branches["country_end"] == country1) & (branches["country_or"] == country2)].index:
        vl = branches.loc[line, "voltage_level2_id"]
        if len(branches[branches["voltage_level1_id"]== vl]) + len(branches[branches["voltage_level2_id"]== vl]) < 2:
            false_border.append(vl)
    branches.loc[branches["voltage_level2_id"].isin(false_border), "country_end"] = country2
    false_border = []
    for line in branches[(branches["country_end"] == country2) & (branches["country_or"] == country1)].index:
        vl = branches.loc[line, "voltage_level1_id"]
        if len(branches[branches["voltage_level1_id"]== vl]) + len(branches[branches["voltage_level2_id"]== vl]) < 2:
            false_border.append(vl)
    branches.loc[branches["voltage_level1_id"].isin(false_border), "country_end"] = country2

    branches = branches[(branches["country_or"].isin([country1, country2])) & (branches["country_end"].isin([country1, country2]))]
    branches_border = branches[branches["country_or"] != branches["country_end"]].copy()
    branches_border.loc[branches_border["country_or"] == country1, f"p_{country1}_to_{country2}"] = branches_border["p1"]
    branches_border.loc[branches_border["country_or"] == country2, f"p_{country1}_to_{country2}"] = branches_border["p2"]
    ac_exchange = branches_border[f"p_{country1}_to_{country2}"].sum()
    # print(branches_frontier)

    hvdcs_border.loc[hvdcs_border["country_or"] == country1, f"p_{country1}_to_{country2}"] = hvdcs_border["p_or"] * hvdcs_border["exchange_sign"]
    hvdcs_border.loc[hvdcs_border["country_or"] == country2, f"p_{country1}_to_{country2}"] = hvdcs_border["p_end"] * hvdcs_border["exchange_sign"]
    hvdc_exchange = hvdcs_border[f"p_{country1}_to_{country2}"].sum()
    if network.per_unit:
        hvdc_exchange *= 100
        ac_exchange *= 100
    return {"total_exchange":hvdc_exchange + ac_exchange,
            "ac_exchange":ac_exchange, "hvdc_exchange":hvdc_exchange}


def apply_contingency_modification(network: nt.Network, case_name: str,
                                   contingency_element_type: str, hvdc_lines_ac_emulation: set,
                                   status: bool):
    """Open the given line in the network"""
    if contingency_element_type == "":
        return

    if contingency_element_type == "ac_line":
        network.update_branches(id=case_name, connected1=status, connected2=status)
    elif contingency_element_type == "transformer":
        network.update_2_windings_transformers(id=case_name, connected1=status, connected2=status)
    elif contingency_element_type == "hvdc_line":
        network.update_hvdc_lines(id=case_name, connected1=status, connected2=status)
        # HVDC line has also an equivalent AC line
        if case_name in hvdc_lines_ac_emulation:
            ac_equivalent_line = "ac_eq_line_" + case_name
            network.update_branches(id=ac_equivalent_line, connected1=status, connected2=status)


def define_slack_bus(network:nt.Network, slack_vl_id:str, slack_bus_id:str):
    """Create a slack bus at the given node"""

    existant_slack_terminal = network.get_extensions("slackTerminal")
    if len(existant_slack_terminal) != 0:
        network.remove_extensions("slackTerminal", existant_slack_terminal.index.to_list())
    slack_bus_load_id = slack_bus_id + "_slack_load"

    try:
        network.create_loads(id=slack_bus_load_id, voltage_level_id=slack_vl_id, bus_id=slack_bus_id, p0=0, q0=0)
    except PyPowsyblError: # Network in nodebreaker
        busbar = network.get_busbar_sections()
        busbar_slack = busbar[busbar["voltage_level_id"] == slack_vl_id]
        nt.create_load_bay(network, id=slack_bus_load_id, p0=0, q0=0, bus_or_busbar_section_id=busbar_slack.index[0], position_order=1)
    network.create_extensions("slackTerminal", voltage_level_id=slack_vl_id, element_id=slack_bus_load_id)


def get_branches_limits(network:nt.Network, monitored_branches:set):
    """Create a dictionnary with the current limits of the monitored branches"""
    limits = network.get_current_limits()
    # limits = network.get_operational_limits()
    remaining_branches = monitored_branches.difference(set(limits.index.get_level_values(0)))
    print(f"Branches with no defined limits: {remaining_branches}")

    limits = limits.loc[list(monitored_branches.difference(remaining_branches))]
    # Creating a dictionnary {branch: {limit_name:value}}
    monitored_branches_limits = limits.groupby(level=0).apply(lambda df: df.xs(df.name)["value"].to_dict()).to_dict()

    for branch, br_limits in monitored_branches_limits.items():
        min_limit = min(br_limits.values())
        if br_limits.get("permanent_limit", 10000) > min_limit:
            br_limits["permanent_limit"] = min_limit
            print(f"Line {branch} has strange permanent_limit :\n{limits.loc[branch]}")
    return monitored_branches_limits


def get_pst_data(network:nt.Network, active_psts_ids):
    """Get important PST data for the optimization
    Need to do that not in pu"""
    network.per_unit = False
    pst_df = network.get_phase_tap_changers()
    pst_angles = network.get_phase_tap_changer_steps()
    pst_dict = {}
    for pst_name, pst_data in pst_df.loc[active_psts_ids,:].iterrows():
        alpha_min = pst_angles.loc[(pst_name, pst_data["low_tap"]), "alpha"]
        alpha_max = pst_angles.loc[(pst_name, pst_data["high_tap"]), "alpha"]
        if alpha_max < alpha_min:
            alpha_min, alpha_max = alpha_max, alpha_min
        alpha_0 = pst_angles.loc[(pst_name, pst_data["tap"]), "alpha"]
        pst_dict[pst_name] = {"min":alpha_min, "max": alpha_max, "referenceSetpoint":alpha_0}
    network.per_unit = True
    return pst_dict


def get_hvdc_data(hvdc_df:pd.DataFrame, active_hvdc_lines_ids:list, per_unit=True):
    """Get needed HVDC data for the optimization"""

    hvdc_df = hvdc_df.loc[active_hvdc_lines_ids]
    # Mapped coupled HVDCs : Two parallel HVDCS will be considered as one lever in the optimization
    hvdc_map = {merged_hvdc:hvdc_df[hvdc_df["voltage_level_id_or"] == hvdc_origin].index.to_list() \
                for merged_hvdc, hvdc_origin in hvdc_df["voltage_level_id_or"].items() if merged_hvdc.endswith("1")}
    # print(hvdc_map)

    pu_ratio = 100 if per_unit else 1
    hvdc_dict = {merged_hvdc_name: {
                            "referenceSetpoint":pu_ratio*sum(hvdc_df.loc[hvdc_name, "target_p"] * \
                                                        hvdc_df.loc[hvdc_name, "exchange_sign"]
                                                        for hvdc_name in hvdc_list),
                            "min": -pu_ratio*sum(hvdc_df.loc[hvdc_name, "max_p"] for hvdc_name in hvdc_list),
                            "max": pu_ratio*sum(hvdc_df.loc[hvdc_name, "max_p"] for hvdc_name in hvdc_list)
                            }
                        for merged_hvdc_name, hvdc_list in hvdc_map.items()}
    return hvdc_map, hvdc_dict


def launch_sensitivity_analysis(network:nt.Network, monitored_branches_ids:list,
                                redispatchable_generators_ids:list, active_psts_ids:list,
                                ac_eq_line_hvdc_lines_ids:list, parameters:lf.Parameters):
    """Launch sensitivity analysis, with the calculation of four sensitivity matrix"""
    analysis = ss.create_ac_analysis()
    analysis.add_factor_matrix(monitored_branches_ids, redispatchable_generators_ids, [],
                               ss.ContingencyContextType.ALL,
                               ss.SensitivityFunctionType.BRANCH_CURRENT_1,
                               ss.SensitivityVariableType.AUTO_DETECT, "generators")
    analysis.add_factor_matrix(monitored_branches_ids, active_psts_ids, [],
                               ss.ContingencyContextType.ALL,
                               ss.SensitivityFunctionType.BRANCH_CURRENT_1,
                               ss.SensitivityVariableType.AUTO_DETECT, "psts")
    analysis.add_factor_matrix(ac_eq_line_hvdc_lines_ids, redispatchable_generators_ids, [],
                               ss.ContingencyContextType.ALL,
                               ss.SensitivityFunctionType.BRANCH_ACTIVE_POWER_1,
                               ss.SensitivityVariableType.AUTO_DETECT, "generators_ac_eq_line")
    analysis.add_factor_matrix(ac_eq_line_hvdc_lines_ids, active_psts_ids, [],
                               ss.ContingencyContextType.ALL,
                               ss.SensitivityFunctionType.BRANCH_ACTIVE_POWER_1,
                               ss.SensitivityVariableType.AUTO_DETECT, "psts_ac_eq_line")
    # lf.run_ac(network, PARAMS)
    return analysis.run(network, parameters)


def get_hvdc_sensitivities_from_generators(result:ss.AcSensitivityAnalysis, fict_gen:pd.DataFrame,
                                           generators_to_ct:dict, matrix_name:str):
    """Returns a dictionnary of sensitivities of lines on generators injection and on HVDC setpoint
    variation"""
    sensitivities_df = result.get_sensitivity_matrix(matrix_name).round(6).fillna(0)
    hvdc_sensitivities_df = pd.DataFrame(index=fict_gen.index, columns=sensitivities_df.columns)
    for hvdc_name, hvdc_row in fict_gen.iterrows():
        summed_col = sensitivities_df.loc[hvdc_row["end"]] - \
                        sensitivities_df.loc[hvdc_row["origin"]]
        hvdc_sensitivities_df.loc[hvdc_name] = summed_col.values
        sensitivities_df.drop(hvdc_row[["origin", "end"]], inplace=True)

    hvdc_sensitivities_dict = hvdc_sensitivities_df.to_dict()
    countertrading_df = sensitivities_df[sensitivities_df.index.isin(generators_to_ct.keys())]
    countertrading_coefficient = pd.Series(generators_to_ct)
    countertrading_dict = countertrading_df.mul(countertrading_coefficient, axis=0).sum().to_dict()
    gens_sensitivities_dict = sensitivities_df[~sensitivities_df.index.isin(generators_to_ct.keys())].to_dict()
    return hvdc_sensitivities_dict, gens_sensitivities_dict, countertrading_dict


def get_reference_current_dictionnary(result:ss.AcSensitivityAnalysis, matrix_name:str):
    """Returns the reference current in a dictionnary"""
    branches_reference = result.get_reference_matrix(matrix_name).fillna(0)
    branches_reference_dict = branches_reference.rename({"reference_values":"referenceCurrent"}
                                                        ).to_dict()
    return branches_reference_dict


def get_pst_sensitivities(result:ss.AcSensitivityAnalysis, matrix_name:str):
    """Returns the pst sensitivities in a dictionnary"""
    psts_sensitivities_df = result.get_sensitivity_matrix(matrix_name).round(6).fillna(0)
    psts_sensitivities_dict = psts_sensitivities_df.to_dict()
    return psts_sensitivities_dict
