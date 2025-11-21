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

import os
import sys
import json
from time import time
import pypowsybl as pp
import pypowsybl.loadflow as lf
import pandas as pd
from .aux import adjust_network, create_ac_lines_to_simulate_hvdc_ac_emulation
from .aux import define_slack_bus, get_branches_limits, get_pst_data, get_hvdc_data
from .aux import apply_contingency_modification, calculate_exchange, get_border_countries
from .aux import add_generators_at_hvdcs_extremities, hvdc_lines_full_setpoint
from .aux import get_hvdc_sensitivities_from_generators, get_reference_current_dictionnary
from .aux import get_pst_sensitivities, launch_sensitivity_analysis, add_exchange_sign_to_hvdc_df

# Calculate sensitivities

# Paths
#   - to network (.iidm)
#   - to monitored branches (.csv)
#   - to contingencies (.csv)
#   - to slack bus (.csv)
#   - to active psts (.csv)
#   - to redispatchable generators (.csv)
#   - to active hvdc lines (.csv)

debug = True

def add_proportionnal_redispatching(network:pp.network.Network, country1:str, country2:str):
    """Add all generators with more than 10MW of Pmax to the generators and calculate the
    repartition key for countertrading"""
    gens = network.get_generators(attributes=["name", "target_p", "min_p", "max_p", "voltage_level_id"])
    vlvs = network.get_voltage_levels(attributes=["substation_id"])
    subs = network.get_substations(attributes=["country"])

    # Calculating repartition key to change the exchange : the production in country1 increases if the
    # redispatching is positive
    gens = gens[gens["max_p"] > 0.1] # filter for max production > 10 MW
    gens = gens.join(vlvs, on="voltage_level_id")
    gens = gens.join(subs, on="substation_id")
    gens = gens[gens["country"].isin([country1, country2])] # filter for production inside pertinent countries
    gens["repartition_key"] = pd.DataFrame({"diff1": gens["max_p"] - gens["target_p"],
                                            "diff2": gens["target_p"] - gens["min_p"]}).min(axis=1)
    gens["repartition_key"] = gens["repartition_key"].clip(lower=0)
    total_repartition = gens.groupby("country")["repartition_key"].transform("sum")
    gens["repartition_key"] /= total_repartition
    # print(f"Sum of maximal production by country is {repartitions}")

    gens.loc[gens["country"] == country2, "repartition_key"] *= -1

    # Changing injection
    return gens["repartition_key"].to_dict()

# Parameters
PARAMS = lf.Parameters(
    read_slack_bus=True,
    distributed_slack=False,
    connected_component_mode=lf.ConnectedComponentMode.MAIN,
    voltage_init_mode=lf.VoltageInitMode.DC_VALUES,
    provider_parameters={"maxNewtonRaphsonIterations":"500"}
)

def main(data_folder:str, network_path:str, monitored_branches_path:str, contingencies_path:str,
         active_hvdc_lines_path:str, active_psts_path:str = None, slack_bus_path:str = None,
         redispatchable_generators_path:str = None, hvdc_target:float = None,
         force_setpoint:bool = False, maximum_counter_trading:float = 0):
    """Load network and csvs with branch_ids (monitored and contingencies)
    Add contingencies to monitored_branches if they are not already present"""

    timers = {}
    current_time = time()
    network = pp.network.load(f"{data_folder}/{network_path}")
    network.per_unit = True
    network = adjust_network(network)

    # Creates AC equivalent lines for HVDC lines with AC emulation
    # AC line reactance equals 1 / droop and updates the target to be the one from extension
    # INFO: only applies to hvdc lines in which the extension hvdcAngleDroopActivePowerControl is enabled
    # On the contrary, if the setpoint mode is forced the AC emulation of all HVDCs is deactivated
    active_hvdc_lines_ids = pd.read_csv(active_hvdc_lines_path)["hvdc_line_id"].to_list()
    if force_setpoint:
        hvdc_emulation_lines_ids = set()
    else:
        hvdc_emulation_lines_ids = create_ac_lines_to_simulate_hvdc_ac_emulation(network, active_hvdc_lines_ids)
    ac_eq_line_hvdc_lines_ids = ["ac_eq_line_" + hvdc_line_id for hvdc_line_id in hvdc_emulation_lines_ids]

    # Set all HVDCs mode to setpoint, the target being updated if needed.
    hvdc_lines_full_setpoint(network, active_hvdc_lines_ids)
    country1, country2 = get_border_countries(network, active_hvdc_lines_ids)
    print(f"Countries are {country1} and {country2}")
    if hvdc_target is not None:
        hvdc_target_list = [hvdc_target] * len(active_hvdc_lines_ids)
        network.update_hvdc_lines(id=active_hvdc_lines_ids, target_p=hvdc_target_list)

    # Network elements
    network_branches_ids = set(network.get_branches().index)
    network_hvdc_lines_ids = set(network.get_hvdc_lines().index)
    twowd_transformers = network.get_2_windings_transformers().index

    # Monitored branches
    monitored_branches_ids = set(pd.read_csv(monitored_branches_path)["branch_id"])
    if len(monitored_branches_ids) == 0:
        raise ValueError("No monitored branch is present on the network. Add monitored branches that are present in the network in monitored_branches.csv")
    monitored_branches_ids.intersection_update(network_branches_ids)

    # Contingencies
    contingencies = pd.read_csv(contingencies_path)
    contingencies_ac_lines_ids = set(contingencies[contingencies["element_type"] == "ac_line"]["element_id"])
    contingencies_hvdc_lines_ids = set(contingencies[contingencies["element_type"] == "hvdc_line"]["element_id"])
    contingencies_transformer_ids = set(contingencies[contingencies["element_type"] == "transformer"]["element_id"])

    # Filter for contingencies in the network
    contingencies_ac_lines_ids.intersection_update(network_branches_ids)
    contingencies_hvdc_lines_ids.intersection_update(network_hvdc_lines_ids)
    contingencies_transformer_ids.intersection_update(twowd_transformers)


    # Calculate sensis with respect to redispatchable generators
    if redispatchable_generators_path is not None:
        redispatchable_generators_ids = pd.read_csv(redispatchable_generators_path)["generator_id"].to_list()
    else:
        redispatchable_generators_ids = []

    if maximum_counter_trading > 0:
        generators_for_ct = add_proportionnal_redispatching(network, country1, country2)
        redispatchable_generators_ids = list(set(redispatchable_generators_ids).union(generators_for_ct.keys()))
        print(f"Countertrading ratios : {generators_for_ct}")
        counter_trading_info = {"counter_trading": {
                "min":-maximum_counter_trading,
                "max":maximum_counter_trading,
                }}
    else:
        generators_for_ct = {}
        counter_trading_info = {}

    # Calculate sensis with respect to active psts
    if active_psts_path is not None:
        active_psts_ids = pd.read_csv(active_psts_path)["pst_id"].to_list()
    else:
        active_psts_ids = []
    # Change regulation mode of active psts
    network.update_phase_tap_changers(id=active_psts_ids, regulating=[False]*len(active_psts_ids),
                                      regulation_value=[0]*len(active_psts_ids),
                                      regulation_mode=["FIXED_TAP"]*len(active_psts_ids))


    # Define slack bus
    if slack_bus_path is not None:
        slack_bus = pd.read_csv(slack_bus_path).iloc[0]
        slack_bus_voltage_level_id = slack_bus["voltage_level_id"]
        slack_bus_bus_id = slack_bus["bus_id"]
        define_slack_bus(network, slack_bus_voltage_level_id, slack_bus_bus_id)
    else:
        PARAMS.read_slack_bus = False

    timers["network_update"] = time() - current_time
    current_time = time()


    dc_lf = lf.run_dc(network, PARAMS)
    print(f"DC loadflow gives {dc_lf}")
    hvdc_df = add_exchange_sign_to_hvdc_df(network, country1, country2)
    print(f"DC exchange levels are {calculate_exchange(network, hvdc_df, country1, country2)}")


    ac_lf = lf.run_ac(network, PARAMS)
    print(f"Initial AC loadflow is {ac_lf}")
    hvdc_df = add_exchange_sign_to_hvdc_df(network, country1, country2)
    situation_description = calculate_exchange(network, hvdc_df, country1, country2)


    hvdc_map, hvdc_dict = get_hvdc_data(hvdc_df, active_hvdc_lines_ids)
    pst_dict = get_pst_data(network, active_psts_ids)
    elem_vars = {
        "hvdc":hvdc_dict,
        "pst":pst_dict,
        "counterTrading":counter_trading_info
        }
    print(f"AC exchange is {situation_description}")
    print(f"Details on HVDCs : {elem_vars['hvdc']}")
    print(f"Details on PSTs : {elem_vars['pst']}")
    situation_description["country1"] = country1
    situation_description["country2"] = country2

    # Add fictitious generators at extremities of controllable HVDCs
    fict_gen = add_generators_at_hvdcs_extremities(network, list(hvdc_map.keys()))
    redispatchable_generators_ids += set(fict_gen["origin"])
    redispatchable_generators_ids += set(fict_gen["end"])

    # Get values of line current limits
    quad_limits = get_branches_limits(network, monitored_branches_ids)
    monitored_branches_ids = list(monitored_branches_ids)

    cases = ["N"] + list(contingencies_ac_lines_ids) + list(contingencies_hvdc_lines_ids) + \
            list(contingencies_transformer_ids)
    contingency_element_types = [""] + len(contingencies_ac_lines_ids) * ["ac_line"] + \
                                len(contingencies_hvdc_lines_ids) * ["hvdc_line"] + \
                                len(contingencies_transformer_ids) * ["transformer"]

    current_time = time()
    initial_name = "InitialState"
    branches_sensitivities = {branch_name:{} for branch_name in monitored_branches_ids}
    ac_eq_sensitivities = {hvdc_line:{} for hvdc_line in hvdc_emulation_lines_ids}
    for (case_name, contingency_element_type) in zip(cases, contingency_element_types):
        # Apply contingency
        print(f"Contingency is {case_name} / {contingency_element_type}", end="    ")
        network.clone_variant(initial_name, "current_contingency")
        network.set_working_variant("current_contingency")
        apply_contingency_modification(network, case_name, contingency_element_type, hvdc_emulation_lines_ids, False)
        if debug:
            lf_res = lf.run_ac(network, PARAMS)
            # print(f"Result of Loadflow is {lf_res}")
            if lf_res[0].status != lf.ComponentStatus.CONVERGED:
                print(f"\n\n\n\n/!\\ Contingency {case_name} does not allow to calculate any sensitivities... retrying /!\\ \n\n\n\n")
                apply_contingency_modification(network, case_name, contingency_element_type, hvdc_emulation_lines_ids, True)
                print(f"Back to normal?: {lf.run_ac(network, PARAMS)}")
                apply_contingency_modification(network, case_name, contingency_element_type, hvdc_emulation_lines_ids, False)
                lf_res_2 = lf.run_ac(network, PARAMS)
                if lf_res_2[0].status != lf.ComponentStatus.CONVERGED:
                    print("Still not working : skiping")
                    timers[f"Sensi for {case_name}"] = time() - current_time
                    current_time = time()
                    continue

        result = launch_sensitivity_analysis(network, monitored_branches_ids,
                                             redispatchable_generators_ids, active_psts_ids,
                                             ac_eq_line_hvdc_lines_ids, PARAMS)

        hvdc_sensitivities_dict, gens_sensitivities_dict, ct_sensitivities_dict = \
            get_hvdc_sensitivities_from_generators(result, fict_gen, generators_for_ct, "generators")

        hvdc_hvdc_sensitivities_dict, hvdc_gens_sensitivities_dict, hvdc_ct_sensitivities_dict = \
            get_hvdc_sensitivities_from_generators(result, fict_gen, generators_for_ct, "generators_ac_eq_line")

        branches_reference_dict = get_reference_current_dictionnary(result, "generators")
        psts_sensitivities_dict = get_pst_sensitivities(result, "psts")
        hvdc_reference_dict = get_reference_current_dictionnary(result, "generators_ac_eq_line")
        hvdc_psts_sensitivities_dict = get_pst_sensitivities(result, "psts_ac_eq_line")

        for branch_name, ref_current in branches_reference_dict.items():
            branches_sensitivities[branch_name][case_name] = ref_current
            for dic_to_add in [gens_sensitivities_dict, psts_sensitivities_dict,
                               hvdc_sensitivities_dict]:
                branches_sensitivities[branch_name][case_name].update(dic_to_add.get(branch_name, {}))
            branches_sensitivities[branch_name][case_name]["counter_trading"] = \
                ct_sensitivities_dict.get(branch_name, 0)
 
        for hvdc_name in hvdc_emulation_lines_ids:
            hvdc_eq_line = "ac_eq_line_" + hvdc_name
            ac_eq_sensitivities[hvdc_name][case_name] = {}
            for dic_to_add in [hvdc_reference_dict, hvdc_gens_sensitivities_dict,
                               hvdc_psts_sensitivities_dict, hvdc_hvdc_sensitivities_dict]:
                ac_eq_sensitivities[hvdc_name][case_name].update(dic_to_add.get(hvdc_eq_line, {}))
            ac_eq_sensitivities[hvdc_name][case_name]["counter_trading"] = \
                hvdc_ct_sensitivities_dict.get(hvdc_name, 0)
        # print(ac_eq_sensitivities)

        # Undo contingency
        network.set_working_variant(initial_name)
        timers[f"Sensi for {case_name}"] = time() - current_time
        print(f"{timers[f'Sensi for {case_name}']:.3f}")
        current_time = time()

    # print(branches_sensitivities)

    merged_json = {
        "situationDescription":situation_description,
        "sensitivities": {
            "branch":branches_sensitivities,
            "hvdc":ac_eq_sensitivities
        },
        "elemVars":elem_vars,
        "quads":quad_limits
    }

    current_time = time()

    name = round(situation_description["total_exchange"])
    output_filepath = f"{data_folder}/{os.path.basename(network_path).split('.')[0]}_{name}" \
                        f"{'setpoint' if force_setpoint else 'ac_emulation'}.json"
    with open(output_filepath, 'w') as all_data_file:
        json.dump(merged_json, all_data_file, indent=4, sort_keys=True)
    print(f"File written at {os.path.abspath(output_filepath)}")

    timers["JSON writing"] = time() - current_time
    current_time = time()
    print(timers)
    print(f"Total time spent {sum(k for k in timers.values()):.3f}\n"
          f"Mean time spent for one case {sum(timers[f'Sensi for {contingency}'] for contingency in cases) / len(cases)}")

if __name__ == "__main__":
    if len(sys.argv) < 2:
        FILE_PATH = os.path.dirname(__file__)
        DATA_FOLDER = os.path.join(FILE_PATH, "tests/test_data")
        IIDM_NAME = "6_bus_system.xiidm"
    else:
        # the argument must the filepath of the network iidm file
        DATA_FOLDER = os.path.dirname(sys.argv[1])
        IIDM_NAME = os.path.basename(sys.argv[1])
    MONITORED_BRANCHES_PATH = f"{DATA_FOLDER}/monitored_branches.csv"
    CONTINGENCIES_PATH = f"{DATA_FOLDER}/contingencies.csv"
    SLACK_BUS_PATH = f"{DATA_FOLDER}/slack_bus.csv"
    ACTIVE_PSTS_PATH = f"{DATA_FOLDER}/active_psts.csv"
    REDISPATCHABLE_GENERATORS = f"{DATA_FOLDER}/redispatchable_generators.csv"
    HVDC_LINES = f"{DATA_FOLDER}/active_hvdc_lines.csv"
    HDVC_TARGET = None
    FORCE_SETPOINT = False
    main(DATA_FOLDER, IIDM_NAME, MONITORED_BRANCHES_PATH, CONTINGENCIES_PATH, HVDC_LINES,
         ACTIVE_PSTS_PATH, SLACK_BUS_PATH, REDISPATCHABLE_GENERATORS, HDVC_TARGET,
         FORCE_SETPOINT, 500)
 