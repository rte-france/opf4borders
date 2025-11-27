
import pandas as pd
import pytest
import pypowsybl.network as nt
import pypowsybl.loadflow as lf
import sys
import os
from sensitivities.aux import calculate_exchange, create_ac_lines_to_simulate_hvdc_ac_emulation
from sensitivities.aux import hvdc_lines_full_setpoint, add_exchange_sign_to_hvdc_df
from sensitivities.aux import launch_sensitivity_analysis, get_hvdc_sensitivities_from_generators
from sensitivities.aux import add_generators_at_hvdcs_extremities, get_pst_sensitivities
from sensitivities.calculate_sensitivities import PARAMS

IIDM_PATH = os.path.join(os.path.dirname(__file__), "test_data/6_bus_system.xiidm")
# Being in AC (implying many non linearities), the tolerances are set quite high
RELATIVE_TOL = 0.025
ABSOLUTE_TOL = 0.01

def test_ac_equivalent_line_gives_same_result_as_ac_emulation():
    """Test Loadflow before (AC emulation) and after (AC equivalent line and HVDC on p0 setpoint)
    is equivalent"""
    network = nt.load(IIDM_PATH)
    network.per_unit = True
    hvdc_droop = network.get_extensions("hvdcAngleDroopActivePowerControl")
    active_hvdcs = list(hvdc_droop.index)
    hvdc_droop["droop"] = [100, 100]
    hvdc_droop["p0"] = [150, 150]
    hvdc_droop["enabled"] = [True, True]
    network.update_extensions("hvdcAngleDroopActivePowerControl", hvdc_droop)

    network.clone_variant("InitialState", "Test")
    lf.run_ac(network, PARAMS)
    vscs_before = network.get_vsc_converter_stations(attributes=["p","q"])
    hvdc_before = network.get_hvdc_lines(attributes=["converters_mode", "converter_station1_id",
                                                     "converter_station2_id", "connected1",
                                                     "connected2"])
    hvdc_before = hvdc_before.join(vscs_before, on="converter_station1_id")
    hvdc_before = hvdc_before.join(vscs_before, on="converter_station2_id", lsuffix="1",
                                   rsuffix="2")


    network.set_working_variant("Test")
    ac_eq_hvdc = create_ac_lines_to_simulate_hvdc_ac_emulation(network, active_hvdcs)
    ac_eq_hvdc_name = ["ac_eq_line_" + hvdc for hvdc in ac_eq_hvdc]
    hvdc_lines_full_setpoint(network, active_hvdcs)
    lf.run_ac(network, PARAMS)
    vscs_after = network.get_vsc_converter_stations(attributes=["p","q"])
    hvdc_after = network.get_hvdc_lines()
    hvdc_after = hvdc_after.join(vscs_after, on="converter_station1_id")
    hvdc_after = hvdc_after.join(vscs_after, on="converter_station2_id", lsuffix="1", rsuffix="2")

    branches_ac_eq = network.get_branches(attributes=["p1", "p2", "q1", "q2"]).loc[ac_eq_hvdc_name]
    branches_ac_eq["corresponding_hvdc"] = ac_eq_hvdc
    hvdc_after = hvdc_after.join(branches_ac_eq.set_index("corresponding_hvdc"), rsuffix="p0",
                                 lsuffix="kdelta")
    hvdc_after["p1"] = hvdc_after["p1p0"] + hvdc_after["p1kdelta"]
    hvdc_after["q1"] = hvdc_after["q1p0"] + hvdc_after["q1kdelta"]
    hvdc_after["p2"] = hvdc_after["p2p0"] + hvdc_after["p2kdelta"]
    hvdc_after["q2"] = hvdc_after["q2p0"] + hvdc_after["q2kdelta"]
    hvdc_after = hvdc_after[hvdc_before.columns]

    pd.testing.assert_frame_equal(hvdc_after, hvdc_before, rtol=RELATIVE_TOL, atol=ABSOLUTE_TOL)


@pytest.mark.parametrize("exchange_level", [100, 200, 500])
def test_calculate_exchange_only_ac_lines(exchange_level):
    """Test exchange level calculation with two AC lines connecting the border
    
    Network :       ES    /     FR
      exchange_level Ze ----- Ul  -exchange_level
                     |        |
                     Ha       At
                     |        |
                     Aj       He
    """
    network = nt.load(IIDM_PATH)

    gens = network.get_generators(attributes=["target_p"])
    loads = network.get_loads(attributes=["p0"])
    gens["target_p"] = [exchange_level, 0, 0, 0, 0, 0, 0, 0]
    loads["p0"] = [0, exchange_level, 0, 0]
    network.update_generators(gens)
    network.update_loads(loads)

    network.update_hvdc_lines(id=["HERA9AJAX1", "HERA9AJAX1bis"], connected1=[False]*2,
                              connected2=[False]*2)
    network.update_lines(id=["HADESL71ATHEN_ACLS", "HADESL72ATHEN_ACLS"], connected1=[False]*2,
                         connected2=[False]*2)

    lf.run_ac(network)
    hvdcs = add_exchange_sign_to_hvdc_df(network, "FR", "ES")
    exchange = calculate_exchange(network, hvdcs, "FR", "ES")
    assert pytest.approx(-exchange_level, rel=RELATIVE_TOL) == exchange["total_exchange"]
    assert pytest.approx(-exchange_level, rel=RELATIVE_TOL) == exchange["ac_exchange"]


@pytest.mark.parametrize("exchange_level", [100, 200, 500])
def test_calculate_exchange_only_hvdc_lines(exchange_level):
    """Test exchange level calculation with two HVDC lines in setpoint connecting the border
    
    Network :       ES    /     FR
                     Ze       Ul
                     |        |
                     Ha       At exchange_level
                     |        |
     -exchange_level Aj- +/- -He
    """
    network = nt.load(IIDM_PATH)

    gens = network.get_generators(attributes=["target_p"])
    loads = network.get_loads(attributes=["p0"])
    gens["target_p"] = [0, 0, 0, 0, exchange_level, 0, 0, 0]
    loads["p0"] = [0, 0, exchange_level, 0]
    print(gens, loads)
    network.update_generators(gens)
    network.update_loads(loads)

    hvdc_droop = network.get_extensions("hvdcAngleDroopActivePowerControl")
    hvdc_droop["enabled"] = [False, False]
    network.update_extensions("hvdcAngleDroopActivePowerControl", hvdc_droop)
    network.update_hvdc_lines(id=["HERA9AJAX1", "HERA9AJAX1bis"], target_p=[exchange_level/2]*2)
    network.update_lines(id=["HADESL71ATHEN_ACLS", "HADESL72ATHEN_ACLS", "ZEUSL61ULYSS_ACLS",
                             "ZEUSL62ULYSS_ACLS"], connected1=[False]*4, connected2=[False]*4)

    lf.run_ac(network)
    hvdcs = add_exchange_sign_to_hvdc_df(network, "FR", "ES")
    exchange = calculate_exchange(network, hvdcs, "FR", "ES")
    assert pytest.approx(exchange_level, rel=RELATIVE_TOL) == exchange["total_exchange"]
    assert pytest.approx(exchange_level, rel=RELATIVE_TOL) == exchange["hvdc_exchange"]


@pytest.mark.parametrize("exchange_level", [100, 200, 500])
def test_calculate_exchange_ac_and_hvdc_lines(exchange_level):
    """Test exchange level calculation with two HVDC lines in AC emulation and 2 AC lines
    connecting the border
    
    Network :       ES    /     FR
                     Ze       Ul
                     |        |
                     Ha ----- At exchange_level
                     |        |
     -exchange_level Aj- +/- -He
    """
    network = nt.load(IIDM_PATH)

    gens = network.get_generators(attributes=["target_p"])
    loads = network.get_loads(attributes=["p0"])
    gens["target_p"] = [0, 0, 0, 0, exchange_level, 0, 0, 0]
    loads["p0"] = [0, 0, exchange_level, 0]
    print(gens, loads)
    network.update_generators(gens)
    network.update_loads(loads)

    hvdc_droop = network.get_extensions("hvdcAngleDroopActivePowerControl")
    hvdc_droop["enabled"] = [True, True]
    hvdc_droop["droop"] = [50, 50]
    hvdc_droop["p0"] = [100, 100]
    network.update_extensions("hvdcAngleDroopActivePowerControl", hvdc_droop)
    network.update_lines(id=["ZEUSL61ULYSS_ACLS", "ZEUSL62ULYSS_ACLS"],
                         connected1=[False]*2, connected2=[False]*2)

    lf.run_ac(network)
    hvdcs = add_exchange_sign_to_hvdc_df(network, "FR", "ES")
    exchange = calculate_exchange(network, hvdcs, "FR", "ES")
    assert pytest.approx(exchange_level, rel=RELATIVE_TOL) == exchange["total_exchange"]


@pytest.mark.parametrize(["injection_variation", "distributed_slack"], [(1, True), (1, False),
                                                                        (-1, True), (-1, False),
                                                                        (10, True), (10, False),
                                                                        (-10, True), (-10, False)])
def test_ac_line_sensitivity_calculation_generator_in_n(injection_variation:float, distributed_slack:bool):
    """Test sensitivity calculation for a generator"""
    network = nt.load(IIDM_PATH)
    PARAMS.distributed_slack = distributed_slack
    hvdc_droop = network.get_extensions("hvdcAngleDroopActivePowerControl")
    hvdc_droop["enabled"] = [False, False]
    network.update_extensions("hvdcAngleDroopActivePowerControl", hvdc_droop)
    lf.run_ac(network, PARAMS)
    generator_name = "ATHEN7G6_NGU_SM"
    monitored_branches = ["AJAXL71HADES_ACLS", "HADESL71ATHEN_ACLS"]
    branches_init_state = network.get_branches(attributes=["i1"]).loc[monitored_branches]

    result = launch_sensitivity_analysis(network, monitored_branches,
                                         [generator_name], [], [], PARAMS)
    _, gens_sensitivities, _ = get_hvdc_sensitivities_from_generators(result, pd.DataFrame(), {}, "generators")
    print(gens_sensitivities)

    current_target = network.get_generators(attributes=["target_p"]).loc[generator_name]
    network.update_generators(id=generator_name, target_p=current_target + injection_variation)
    lf.run_ac(network, PARAMS)
    branches_after = network.get_branches(attributes=["i1"])
    for branch in monitored_branches:
        assert pytest.approx(injection_variation * gens_sensitivities[branch][generator_name], abs=ABSOLUTE_TOL) == \
            (branches_after.loc[branch, "i1"] - branches_init_state.loc[branch, "i1"])

    PARAMS.distributed_slack = False


@pytest.mark.parametrize(["injection_variation", "distributed_slack"], [(1, True), (1, False),
                                                                        (-1, True), (-1, False),
                                                                        (10, True), (10, False),
                                                                        (-10, True), (-10, False)])
def test_ac_line_sensitivity_calculation_hvdc_in_n(injection_variation:float, distributed_slack:bool):
    """Test sensitivity calculation for an hvdc"""
    network = nt.load(IIDM_PATH)
    PARAMS.distributed_slack = distributed_slack
    hvdc_droop = network.get_extensions("hvdcAngleDroopActivePowerControl")
    hvdc_droop["enabled"] = [False, False]
    network.update_extensions("hvdcAngleDroopActivePowerControl", hvdc_droop)
    hvdc_name = "HERA9AJAX1"
    initial_target = 150
    network.update_hvdc_lines(id=["HERA9AJAX1", "HERA9AJAX1bis"], target_p=[initial_target]*2)
    hvdc_to_fict_gen = add_generators_at_hvdcs_extremities(network, [hvdc_name])
    lf.run_ac(network, PARAMS)
    monitored_branches = ["AJAXL71HADES_ACLS", "HADESL71ATHEN_ACLS"]
    branches_init_state = network.get_branches(attributes=["i1"]).loc[monitored_branches]

    result = launch_sensitivity_analysis(network, monitored_branches,
                                         list(hvdc_to_fict_gen.loc[hvdc_name]), [], [], PARAMS)
    hvdc_sensitivities, *_ = get_hvdc_sensitivities_from_generators(result, hvdc_to_fict_gen, {}, "generators")
    print(hvdc_sensitivities)

    network.update_hvdc_lines(id="HERA9AJAX1", target_p=initial_target + injection_variation)
    lf.run_ac(network, PARAMS)
    branches_after = network.get_branches(attributes=["i1"])
    for branch in monitored_branches:
        assert pytest.approx(injection_variation * hvdc_sensitivities[branch][hvdc_name], rel=RELATIVE_TOL) == \
            (branches_after.loc[branch, "i1"] - branches_init_state.loc[branch, "i1"])

    PARAMS.distributed_slack = False


@pytest.mark.parametrize(["tap_change", "distributed_slack"], [(1, True), (1, False),
                                                               (-1, True), (-1, False),
                                                            #    (5, True), (5, False),
                                                            #    (-5, True), (-5, False)
                                                               ])
def test_ac_line_sensitivity_calculation_pst_in_n(tap_change:float, distributed_slack:bool):
    """Test sensitivity calculation for a pst"""
    network = nt.load(IIDM_PATH)
    network.per_unit = False
    PARAMS.distributed_slack = distributed_slack
    pst_name = "NIREEL61ZEUS_ACLS"
    network.update_phase_tap_changers(id=pst_name, regulating=False, regulation_value=0,
                                      regulation_mode="FIXED_TAP", tap=10)

    pst_angles = network.get_phase_tap_changer_steps()
    alpha_init = pst_angles.loc[(pst_name, 10), "alpha"]
    alpha_after = pst_angles.loc[(pst_name, 10 + tap_change), "alpha"]
    delta_alpha = alpha_after - alpha_init
    lf.run_ac(network, PARAMS)
    monitored_branches = ["AJAXL71HADES_ACLS", "HADESL71ATHEN_ACLS"]
    branches_init_state = network.get_branches(attributes=["i1"]).loc[monitored_branches]

    result = launch_sensitivity_analysis(network, monitored_branches,
                                         [], [pst_name], [], PARAMS)
    pst_sensitivities = get_pst_sensitivities(result, "psts")
    print(pst_sensitivities)

    network.update_phase_tap_changers(id=pst_name, tap=10 + tap_change)
    lf.run_ac(network, PARAMS)
    branches_after = network.get_branches(attributes=["i1"])
    for branch in monitored_branches:
        assert pytest.approx(delta_alpha * pst_sensitivities[branch][pst_name], rel=RELATIVE_TOL) == \
            (branches_after.loc[branch, "i1"] - branches_init_state.loc[branch, "i1"])

    PARAMS.distributed_slack = False
