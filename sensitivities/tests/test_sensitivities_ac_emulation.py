
import pandas as pd
import pytest
import pypowsybl.network as nt
import pypowsybl.loadflow as lf
import sys
import os
from sensitivities.aux import adjust_network, create_ac_lines_to_simulate_hvdc_ac_emulation
from sensitivities.aux import hvdc_lines_full_setpoint, get_reference_flow_dictionnary
from sensitivities.aux import launch_sensitivity_analysis, get_hvdc_sensitivities_from_generators
from sensitivities.aux import add_generators_at_hvdcs_extremities, get_pst_sensitivities
from sensitivities.calculate_sensitivities import PARAMS

IIDM_PATH = os.path.join(os.path.dirname(__file__), "test_data/6_bus_system.xiidm")
# Being in AC (implying many non linearities), the tolerances are set quite high
RELATIVE_TOL = 0.025
ABSOLUTE_TOL = 0.01


@pytest.mark.parametrize(["injection_variation", "distributed_slack"], [(1, True), (1, False),
                                                                        (-1, True), (-1, False),
                                                                        (10, True), (10, False),
                                                                        (-10, True), (-10, False)])
def test_hvdc_line_sensitivity_calculation_generator_in_n(injection_variation:float, distributed_slack:bool):
    """Test sensitivity calculation for an HVDC in AC emulation.
    Comparing injection power with the AC emulation after a variation in generation
    and the expected power given by the sensitivity calculation (after the same variation)"""
    network = nt.load(IIDM_PATH)
    network = adjust_network(network) # no loss on HVDCs
    PARAMS.distributed_slack = distributed_slack
    network.per_unit = True
    hvdc_droop = network.get_extensions("hvdcAngleDroopActivePowerControl")
    active_hvdcs = list(hvdc_droop.index)
    hvdc_droop["droop"] = [100, 100]
    hvdc_droop["p0"] = [150, 150]
    hvdc_droop["enabled"] = [True, True]
    network.update_extensions("hvdcAngleDroopActivePowerControl", hvdc_droop)
    generator_name = "ATHEN7G6_NGU_SM"
    lf.run_ac(network, PARAMS)
    network.clone_variant("InitialState", "AcEqLine")
    current_target = network.get_generators(attributes=["target_p"]).loc[generator_name]
    network.update_generators(id=generator_name, target_p=current_target + injection_variation)
    lf.run_ac(network, PARAMS)
    vscs_original = network.get_vsc_converter_stations(attributes=["p","q"])
    hvdc_original = network.get_hvdc_lines(attributes=["converters_mode", "converter_station1_id",
                                                       "converter_station2_id", "connected1",
                                                       "connected2"])
    hvdc_original = hvdc_original.join(vscs_original, on="converter_station1_id")
    hvdc_original = hvdc_original.join(vscs_original, on="converter_station2_id", lsuffix="1",
                                       rsuffix="2")

    network.set_working_variant("AcEqLine")
    ac_eq_hvdc = create_ac_lines_to_simulate_hvdc_ac_emulation(network, active_hvdcs)
    ac_eq_hvdc_name = ["ac_eq_line_" + hvdc for hvdc in ac_eq_hvdc]
    hvdc_lines_full_setpoint(network, active_hvdcs)

    result = launch_sensitivity_analysis(network, [], [generator_name], [], ac_eq_hvdc_name, PARAMS)
    _, gens_sensitivities, _ = get_hvdc_sensitivities_from_generators(result, pd.DataFrame(), {},
                                                                      "generators_ac_eq_line")
    ref_flow = get_reference_flow_dictionnary(result, "generators_ac_eq_line")
    print(gens_sensitivities, ref_flow)

    for hvdc in ac_eq_hvdc_name:
        assert pytest.approx(100*hvdc_original.loc[hvdc[11:], "p1"], rel=RELATIVE_TOL) == \
            (150 + ref_flow[hvdc]["referenceCurrent"] + injection_variation * gens_sensitivities[hvdc][generator_name])

    PARAMS.distributed_slack = False


@pytest.mark.parametrize(["injection_variation", "distributed_slack"], [(1, True), (1, False),
                                                                        (-1, True), (-1, False),
                                                                        (10, True), (10, False),
                                                                        (-10, True), (-10, False)])
def test_hvdc_line_sensitivity_calculation_hvdc_in_n(injection_variation:float, distributed_slack:bool):
    """Test sensitivity calculation for an HVDC in AC emulation.
    Comparing injection power with the AC emulation after a variation in HVDC setpoint (p0)
    and the expected power given by the sensitivity calculation (after the same variation)"""
    network = nt.load(IIDM_PATH)
    network = adjust_network(network) # no loss on HVDCs
    PARAMS.distributed_slack = distributed_slack
    network.per_unit = True
    hvdc_droop = network.get_extensions("hvdcAngleDroopActivePowerControl")
    active_hvdcs = list(hvdc_droop.index)
    hvdc_droop["droop"] = [100, 100]
    hvdc_droop["p0"] = [150, 150]
    hvdc_droop["enabled"] = [True, True]
    network.update_extensions("hvdcAngleDroopActivePowerControl", hvdc_droop)
    hvdc_name = "HERA9AJAX1"
    lf.run_ac(network, PARAMS)
    network.clone_variant("InitialState", "AcEqLine")

    hvdc_droop["p0"] = [150, 150 + injection_variation]
    network.update_extensions("hvdcAngleDroopActivePowerControl", hvdc_droop)
    lf.run_ac(network, PARAMS)
    vscs_original = network.get_vsc_converter_stations(attributes=["p","q"])
    hvdc_original = network.get_hvdc_lines(attributes=["converters_mode", "converter_station1_id",
                                                       "converter_station2_id", "connected1",
                                                       "connected2"])
    hvdc_original = hvdc_original.join(vscs_original, on="converter_station1_id")
    hvdc_original = hvdc_original.join(vscs_original, on="converter_station2_id", lsuffix="1",
                                       rsuffix="2")

    network.set_working_variant("AcEqLine")
    hvdc_to_fict_gen = add_generators_at_hvdcs_extremities(network, [hvdc_name])
    ac_eq_hvdc = create_ac_lines_to_simulate_hvdc_ac_emulation(network, active_hvdcs)
    ac_eq_hvdc_name = ["ac_eq_line_" + hvdc for hvdc in ac_eq_hvdc]
    hvdc_lines_full_setpoint(network, active_hvdcs)

    result = launch_sensitivity_analysis(network, [], list(hvdc_to_fict_gen.loc[hvdc_name]),
                                         [], ac_eq_hvdc_name, PARAMS)
    hvdc_sensitivities, *_ = get_hvdc_sensitivities_from_generators(result, hvdc_to_fict_gen, {},
                                                                    "generators_ac_eq_line")
    ref_flow = get_reference_flow_dictionnary(result, "generators_ac_eq_line")
    print(hvdc_sensitivities, ref_flow)

    for hvdc in ac_eq_hvdc_name:
        assert pytest.approx(100*hvdc_original.loc[hvdc[11:], "p1"], rel=RELATIVE_TOL) == \
            (150 + ref_flow[hvdc]["referenceCurrent"] + injection_variation * hvdc_sensitivities[hvdc][hvdc_name])

    PARAMS.distributed_slack = False


# @pytest.mark.parametrize(["tap_change", "distributed_slack"], [(1, True), (1, False),
#                                                                (-1, True), (-1, False),
#                                                             #    (5, True), (5, False),
#                                                             #    (-5, True), (-5, False)
#                                                                ])
# def test_hvdc_line_sensitivity_calculation_pst_in_n(tap_change:float, distributed_slack:bool):
#     """Test sensitivity calculation for a pst"""
#     network = nt.load(IIDM_PATH)
#     network.per_unit = False
#     PARAMS.distributed_slack = distributed_slack
#     pst_name = "NIREEL61ZEUS_ACLS"
#     network.update_phase_tap_changers(id=pst_name, regulating=False, regulation_value=0,
#                                       regulation_mode="FIXED_TAP", tap=10)

#     pst_angles = network.get_phase_tap_changer_steps()
#     alpha_init = pst_angles.loc[(pst_name, 10), "alpha"]
#     alpha_after = pst_angles.loc[(pst_name, 10 + tap_change), "alpha"]
#     delta_alpha = alpha_after - alpha_init
#     lf.run_ac(network, PARAMS)
#     monitored_branches = ["AJAXL71HADES_ACLS", "HADESL71ATHEN_ACLS"]
#     branches_init_state = network.get_branches(attributes=["i1"]).loc[monitored_branches]

#     result = launch_sensitivity_analysis(network, monitored_branches,
#                                          [], [pst_name], [], PARAMS)
#     pst_sensitivities = get_pst_sensitivities(result, "psts")
#     print(pst_sensitivities)

#     network.update_phase_tap_changers(id=pst_name, tap=10 + tap_change)
#     lf.run_ac(network, PARAMS)
#     branches_after = network.get_branches(attributes=["i1"])
#     for branch in monitored_branches:
#         assert pytest.approx(delta_alpha * pst_sensitivities[branch][pst_name], rel=RELATIVE_TOL) == \
#             (branches_after.loc[branch, "i1"] - branches_init_state.loc[branch, "i1"])

#     PARAMS.distributed_slack = False
