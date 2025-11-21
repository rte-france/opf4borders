#=
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
=#
using  JuMP, Xpress

function create_model(quiet::Bool, network::NETWORK, set_of_hvdc::Set, set_of_pst::Set, set_of_counter::Set,
                      set_of_quad_inc::Set, set_of_hvdc_inc::Set, dict_of_quad_inc_sensi::Dict, ist_margin)
    model = Model(Xpress.Optimizer)
    MOI.set(model, MOI.Silent(), quiet)

    @variables(model,
    begin
        network._hvdcs[hvdc].pMin -  network._hvdcs[hvdc].elemP0 <=
            delta_P0[hvdc in set_of_hvdc] <=
            network._hvdcs[hvdc].pMax - network._hvdcs[hvdc].elemP0
    end);

    @variables(model,
    begin
        network._psts[pst].alphaMin -  network._psts[pst].alpha0 <=
            delta_alpha[pst in set_of_pst] <=
            network._psts[pst].alphaMax - network._psts[pst].alpha0
    end);

    @variables(model,
    begin
        network._countertrading[counter].pMin <= 
            counter_trading[counter in set_of_counter] <=
            network._countertrading[counter].pMax
    end);

    @variables(model,
    begin
        counter_trading_abs[counter in set_of_counter]
    end);

    @variables(model,
    begin
        current_slack_pos[(quad, inc) in set_of_quad_inc]
    end);

    @variables(model,
    begin
        current_slack_neg[(quad, inc) in set_of_quad_inc]
    end);

    @variables(model,
    begin
        current_slack[(quad, inc) in set_of_quad_inc]
    end);

    # @variables(model,
    # begin
    #     0 <= hvdc_slack_pos[hvdc in set_of_hvdc]
    # end);

    # @variables(model,
    # begin
    #     0 <= hvdc_slack_neg[hvdc in set_of_hvdc]
    # end);

    # @variables(model,
    # begin
    #     0 <= hvdc_slack[hvdc in set_of_hvdc]
    # end);

    # @constraints(model,
    # begin
    #     Unique_Hvdc_Slack_Neg[hvdc in set_of_hvdc],
    #     hvdc_slack[hvdc] <= hvdc_slack_neg[hvdc]
    # end)

    # @constraints(model,
    # begin
    #     Unique_Hvdc_Slack_Pos[hvdc in set_of_hvdc],
    #     hvdc_slack[hvdc] <= hvdc_slack_pos[hvdc]
    # end)

    @constraints(model,
    begin
        Unique_Current_Slack_Neg[(quad, inc) in set_of_quad_inc],
        current_slack[(quad, inc)] <= current_slack_neg[(quad, inc)]
    end)

    @constraints(model,
    begin
        Unique_Current_Slack_Pos[(quad, inc) in set_of_quad_inc],
        current_slack[(quad, inc)] <= current_slack_pos[(quad, inc)]
    end)

    @constraints(model,
    begin
        Positive_Value_Counter_Trading[counter in set_of_counter],
        counter_trading[counter] <= counter_trading_abs[counter]
    end)

    @constraints(model,
    begin
        Negative_Value_Counter_Trading[counter in set_of_counter],
        - counter_trading[counter] <= counter_trading_abs[counter]
    end)

    @variable(model, minimum_margin)

    @constraints(model,
    begin
        Power_Max_Pos[(hvdc, inc) in set_of_hvdc_inc],
        0 <= - network._hvdcs[hvdc].pMin +
            (network._hvdcs[hvdc].elemP0 + network._sensi[hvdc, inc, _REFERENCE_CURRENT] +
            delta_P0[hvdc] +
            sum(val * delta_alpha[pst] for (pst, val) in dict_of_quad_inc_sensi[hvdc, inc] if pst in set_of_pst) +
            sum(val * delta_P0[hvdc_other] for (hvdc_other, val) in dict_of_quad_inc_sensi[hvdc, inc] if hvdc_other in set_of_hvdc)) +
            sum(val * counter_trading[counter] for (counter, val) in dict_of_quad_inc_sensi[hvdc, inc] if counter in set_of_counter)
    end
    )

    @constraints(model,
    begin
        Power_Max_Neg[(hvdc, inc) in set_of_hvdc_inc],
        # (inc == _BASECASE : hvdc_slack_neg[hvdc] : 0) <= network._hvdcs[hvdc].pMax -
        0 <= network._hvdcs[hvdc].pMax -
            (network._hvdcs[hvdc].elemP0 + network._sensi[hvdc, inc, _REFERENCE_CURRENT] +
            delta_P0[hvdc] +
            sum(val * delta_alpha[pst] for (pst, val) in dict_of_quad_inc_sensi[hvdc, inc] if pst in set_of_pst) +
            sum(val * delta_P0[hvdc_other] for (hvdc_other, val) in dict_of_quad_inc_sensi[hvdc, inc] if hvdc_other in set_of_hvdc)) +
            sum(val * counter_trading[counter] for (counter, val) in dict_of_quad_inc_sensi[hvdc, inc] if counter in set_of_counter)
    end
    )

    @constraints(model,
    begin
        Current_Max_Pos[(quad, inc) in set_of_quad_inc], # should check that the ref current is not null (= opened line)
        current_slack_pos[(quad, inc)] <= ist_margin * network._quads[quad].limits[_PERMANENT_LIMIT] -
            (network._sensi[quad, inc, _REFERENCE_CURRENT] +
            sum(val * delta_alpha[pst] for (pst, val) in dict_of_quad_inc_sensi[quad, inc] if pst in set_of_pst) +
            sum(val * delta_P0[hvdc] for (hvdc, val) in dict_of_quad_inc_sensi[quad, inc] if hvdc in set_of_hvdc)) +
            sum(val * counter_trading[counter] for (counter, val) in dict_of_quad_inc_sensi[quad, inc] if counter in set_of_counter)
    end
    )

    @constraints(model,
    begin
        Current_Max_Neg[(quad, inc) in  set_of_quad_inc],
        current_slack_neg[(quad, inc)] <= ist_margin * network._quads[quad].limits[_PERMANENT_LIMIT] +
            network._sensi[quad, inc, _REFERENCE_CURRENT] +
            sum(val * delta_alpha[pst] for (pst, val) in dict_of_quad_inc_sensi[quad, inc] if pst in set_of_pst) +
            sum(val * delta_P0[hvdc] for (hvdc, val) in dict_of_quad_inc_sensi[quad, inc] if hvdc in set_of_hvdc) +
            sum(val * counter_trading[counter] for (counter, val) in dict_of_quad_inc_sensi[quad, inc] if counter in set_of_counter)
    end
    )

    @constraints(model,
    begin
        Minimum_Margin_Neg[(quad, inc) in  set_of_quad_inc],
        minimum_margin <= current_slack[(quad, inc)]
    end
    )

    # first check if a safe N / N-1 one state exists
    @objective(model, MAX_SENSE, minimum_margin)

    return model, delta_P0, delta_alpha, minimum_margin, current_slack#, hvdc_slack
end
