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

using JSON
using JuMP
using Xpress
using PrettyPrint


_BASECASE  = "N"
_SENSI = "sensi"
_BRANCH = "branch"
_QUADS = "quads"
_ELEMVARS="elemVars"
_PERMANENT_LIMIT = "permanent_limit"
_MIN = "min"
_MAX = "max"
_ELEMP0 = "ref_setpoint"
_HVDC = "hvdc"
_PST = "pst"
_REFERENCE_CURRENT = "referenceCurrent"

struct HVDC
    name::String;
    pMin::Float64;
    pMax::Float64;
    elemP0::Float64;
end

struct PST
    name::String;
    alphaMin::Float64;
    alphaMax::Float64;
    alpha0::Float64;
end

struct QUAD
    name::String
    limits::Dict{String, Float64}
    # permanentLimit::Float64;
end

struct NETWORK
    _hvdcs::Dict{String, HVDC};
    _psts::Dict{String, PST};
    _quads::Dict{String, QUAD};

    _sensi::Dict{Tuple{String, String, String}, Float64};

    NETWORK() = new(Dict{String, HVDC}(), Dict{String, PST}(), Dict{String, QUAD}(),
                    Dict{Tuple{String, String, String}, Float64}());

end

function read_json(file_name::String)
    """Read the unique json entry and populate the different structures needed for the optimization"""
    json = JSON.parsefile(file_name)
    JSON_KEYS = [_QUADS, _ELEMVARS,_SENSI]
    network = NETWORK()

    for (name, quad_values) in json[_QUADS]
        network._quads[name] = QUAD(name, Dict(_PERMANENT_LIMIT => quad_values[_PERMANENT_LIMIT]))
    end

    for (hvdcOrPst, v1) in json[_ELEMVARS], (name, v2) in v1
        if hvdcOrPst == _HVDC
                network._hvdcs[name] = HVDC(name, v2[_MIN], v2[_MAX], v2[_ELEMP0])
        elseif hvdcOrPst == _PST
                network._psts[name] = PST(name, v2[_MIN], v2[_MAX], v2[_ELEMP0])
        end
    end
    # (sensi, branch/hvdc, INC, element) ==> value
    for (branch, v2) in json[_SENSI][_BRANCH], (INC,v3) in v2, (element,v4) in v3
        network._sensi[branch,  INC, element] = v4
    end
    for (hvdc, v2) in json[_SENSI][_HVDC], (INC,v3) in v2, (element,v4) in v3
        network._sensi[hvdc,  INC, element] = v4
    end
    return network
end


function create_model(quiet::Bool, network::NETWORK, set_of_hvdc::Set, set_of_pst::Set,
                      set_of_quad_inc::Set, set_of_hvdc_inc::Set ,dict_of_quad_inc_sensi::Dict)
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
        current_slack_pos[(quad, inc) in set_of_quad_inc]
    end);

    @variables(model,
    begin
        current_slack_neg[(quad, inc) in set_of_quad_inc]
    end);

    @variable(model, minimum_margin)

    @constraints(model,
    begin
        Power_Max_Pos[(hvdc, inc) in set_of_hvdc_inc],
        0 <= network._hvdcs[hvdc].pMax +
            (network._hvdcs[hvdc].elemP0 + network._sensi[hvdc, inc, _REFERENCE_CURRENT] +
            delta_P0[hvdc] +
            sum(val * delta_alpha[pst] for (pst, val) in dict_of_quad_inc_sensi[hvdc, inc] if pst in set_of_pst) +
            sum(val * delta_P0[hvdc_other] for (hvdc_other, val) in dict_of_quad_inc_sensi[hvdc, inc] if hvdc_other in set_of_hvdc))
    end
    )

    @constraints(model,
    begin
        Power_Max_Neg[(hvdc, inc) in set_of_hvdc_inc],
        0 <= network._hvdcs[hvdc].pMax -
            (network._hvdcs[hvdc].elemP0 + network._sensi[hvdc, inc, _REFERENCE_CURRENT] +
            delta_P0[hvdc] +
            sum(val * delta_alpha[pst] for (pst, val) in dict_of_quad_inc_sensi[hvdc, inc] if pst in set_of_pst) +
            sum(val * delta_P0[hvdc_other] for (hvdc_other, val) in dict_of_quad_inc_sensi[hvdc, inc] if hvdc_other in set_of_hvdc))
    end
    )

    @constraints(model,
    begin
        Current_Max_Pos[(quad, inc) in set_of_quad_inc], # should check that the ref current is not null (= opened line)
        current_slack_pos[(quad, inc)] <= network._quads[quad].limits[_PERMANENT_LIMIT] -
            (network._sensi[quad, inc, _REFERENCE_CURRENT] +
            sum(val * delta_alpha[pst] for (pst, val) in dict_of_quad_inc_sensi[quad, inc] if pst in set_of_pst) +
            sum(val * delta_P0[hvdc] for (hvdc, val) in dict_of_quad_inc_sensi[quad, inc] if hvdc in set_of_hvdc))
    end
    )

    @constraints(model,
    begin
        Current_Max_Neg[(quad, inc) in  set_of_quad_inc],
        current_slack_neg[(quad, inc)] <= network._quads[quad].limits[_PERMANENT_LIMIT] +
            network._sensi[quad, inc, _REFERENCE_CURRENT] +
            sum(val * delta_alpha[pst] for (pst, val) in dict_of_quad_inc_sensi[quad, inc] if pst in set_of_pst) +
            sum(val * delta_P0[hvdc] for (hvdc, val) in dict_of_quad_inc_sensi[quad, inc] if hvdc in set_of_hvdc)
    end
    )

    @constraints(model,
    begin
        Minimum_Margin_Neg[(quad, inc) in  set_of_quad_inc],
        minimum_margin <= current_slack_neg[(quad, inc)]
    end
    )

    @constraints(model,
    begin
        Minimum_Margin_Pos[(quad, inc) in  set_of_quad_inc],
        minimum_margin <= current_slack_pos[(quad, inc)]
    end
    )

    # first check if a safe N / N-1 one state exists
    @objective(model, MAX_SENSE, minimum_margin)

    return model, delta_P0, delta_alpha, minimum_margin, current_slack_neg, current_slack_pos
end

function  write_optimization_results(objective_val::Float64, delta_P0::JuMP.Containers.DenseAxisArray, delta_alpha::JuMP.Containers.DenseAxisArray,
                                     network::NETWORK, set_of_hvdc::Set, set_of_pst::Set)
    P0_value = Dict(hvdc => network._hvdcs[hvdc].elemP0+value(delta_P0[hvdc]) for hvdc in set_of_hvdc)
    alpha0_value = Dict(pst => network._psts[pst].alpha0+value(delta_alpha[pst]) for pst in set_of_pst)
    return Dict("objective_value" => objective_val,
                "P0" => P0_value,
                "alpha0" => alpha0_value)    
end

function  write_calculated_line_currents(delta_P0::JuMP.Containers.DenseAxisArray, delta_alpha::JuMP.Containers.DenseAxisArray,
                                         network::NETWORK, set_of_hvdc::Set, set_of_pst::Set, set_of_quad_inc::Set,
                                         dict_of_quad_inc_sensi::Dict)
    dictionnary = Dict()
    for (quad, inc) in set_of_quad_inc
        if ! haskey(dictionnary, inc)
            dictionnary[inc] = Dict()
        end
        dictionnary[inc][quad] = network._sensi[quad, inc, _REFERENCE_CURRENT] +
                                 sum(val * value(delta_P0[hvdc]) for (hvdc, val) in dict_of_quad_inc_sensi[quad, inc] if hvdc in set_of_hvdc)
        if !isempty(set_of_pst)
            dictionnary[inc][quad] += sum(val * value(delta_alpha[pst]) for (pst, val) in dict_of_quad_inc_sensi[quad, inc] if pst in set_of_pst)
        end
    end
    return dictionnary
end

function launch_optimization(file_name::String)
    launch_optimization(file_name, "results.json", Set(), true)
end

function launch_optimization(file_name::String, results_file_name::String)
    launch_optimization(file_name, results_file_name, Set(), true)
end

function launch_optimization(file_name::String, results_file_name::String, controllable_hvdcs::Set, pst_control::Bool)
    network = read_json(file_name);
    if isempty(controllable_hvdcs)
        set_of_hvdc = Set(keys(network._hvdcs))
    else
        set_of_hvdc = controllable_hvdcs
    end
    if pst_control
        set_of_pst = Set(keys(network._psts))
    else
        set_of_pst = Set()
    end

    set_of_quad_inc = Set();
    set_of_hvdc_inc = Set();
    dict_of_quad_inc_sensi = Dict();

    for (quad, inc, _) in keys(network._sensi)
        # The quad is a monitored AC line or a PST
        if haskey(network._quads, quad) && ! haskey(dict_of_quad_inc_sensi, (quad, inc))
            push!(set_of_quad_inc, (quad, inc))
            dict_of_quad_inc_sensi[quad, inc] = Dict()
        end
        # The quad is an HVDC with AC emulation
        if haskey(network._hvdcs, quad) && !((quad, inc) in set_of_hvdc_inc)
            push!(set_of_hvdc_inc, (quad, inc))
            dict_of_quad_inc_sensi[quad, inc] = Dict()
        end
    end
    no_limits_quad = Set()
    for ((quad, inc, element), val) in network._sensi
        if haskey(dict_of_quad_inc_sensi, (quad, inc))
            if element != _REFERENCE_CURRENT
                dict_of_quad_inc_sensi[quad, inc][element] = val
            end
        else
            push!(no_limits_quad, quad)
        end
    end
    for quad in no_limits_quad
        println("no limits for line $quad but sensi provided")
    end

    model, delta_P0, delta_alpha, minimum_margin, current_slack_neg, current_slack_pos =
        create_model(true, network, set_of_hvdc, set_of_pst, set_of_quad_inc, set_of_hvdc_inc, dict_of_quad_inc_sensi)

    optimize!(model)
    minimum_margin_possible = value(minimum_margin)
    println("The minimum margin possible is (if negative this is an issue) ", minimum_margin_possible)

    if minimum_margin_possible > 0
        # possible to satisfy all the constraints
        fix(minimum_margin, 0.0)
    else
        problematic_contigencies = Set()
        problematic_contigencies_overloads = Dict()
        for (quad, inc) in set_of_quad_inc
            if value(current_slack_neg[(quad, inc)]) < 0 || value(current_slack_pos[(quad, inc)]) < 0
                push!(problematic_contigencies, inc)
                if ! haskey(problematic_contigencies_overloads, inc)
                    problematic_contigencies_overloads[inc] = Set()
                end
                push!(problematic_contigencies_overloads[inc], quad)
            end
        end
        fix(minimum_margin, minimum_margin_possible - 1)
        println("The following contingencies causes trouble: ", problematic_contigencies)
        println("The following contingencies causes trouble: ", problematic_contigencies_overloads)
        println("Removing them from optimization...")
        for contingency in problematic_contigencies
            set_objective(model, MAX_SENSE,
                          sum(current_slack_neg[(quad, contingency)] +
                              current_slack_pos[(quad, contingency)] for (quad, contingency) in set_of_quad_inc))
            optimize!(model)
            println("\nThe HVDC setpoints maximizing the margins for $contingency contingency is: ",
                    Dict(hvdc =>network._hvdcs[hvdc].elemP0+value(delta_P0[hvdc]) for hvdc in set_of_hvdc))
            println("Which corresponds to PST setpoints : ", 
                    Dict(pst =>network._psts[pst].alpha0+value(delta_alpha[pst]) for pst in set_of_pst))
        end
        for (quad, inc) in set_of_quad_inc
            if ! haskey(problematic_contigencies_overloads, inc) || ! (quad in problematic_contigencies_overloads[inc])
                fix(current_slack_neg[(quad, inc)], 0.0)
                fix(current_slack_pos[(quad, inc)], 0.0)
            end
        end
    end


    P0 = Dict(hvdc => network._hvdcs[hvdc].elemP0 for hvdc in set_of_hvdc)
    alpha0 = Dict(pst => network._psts[pst].alpha0 for pst in set_of_pst)
    results_dict = Dict("reference" => Dict("objective_value" => 0, "P0" => P0, "alpha0" => alpha0))
    set_objective(model, MIN_SENSE, sum(delta_P0[hvdc] for hvdc in set_of_hvdc))
    optimize!(model)
    results_dict["min_min"] = write_optimization_results(objective_value(model), delta_P0, delta_alpha,
                                                         network, set_of_hvdc, set_of_pst)

    set_objective(model, MIN_SENSE, sum(- delta_P0[hvdc] for hvdc in set_of_hvdc))
    optimize!(model)
    results_dict["max_max"] = write_optimization_results(objective_value(model), delta_P0, delta_alpha,
                                                         network, set_of_hvdc, set_of_pst)

    set_objective(model, MIN_SENSE, sum((-1)^index * delta_P0[hvdc] for (index,hvdc) in enumerate(set_of_hvdc)))
    optimize!(model)
    results_dict["max_min"] = write_optimization_results(objective_value(model), delta_P0, delta_alpha,
                                                         network, set_of_hvdc, set_of_pst)

    set_objective(model, MIN_SENSE, sum((-1)^(index+1) * delta_P0[hvdc] for (index,hvdc) in enumerate(set_of_hvdc)))
    optimize!(model)
    results_dict["min_max"] = write_optimization_results(objective_value(model), delta_P0, delta_alpha,
                                                         network, set_of_hvdc, set_of_pst)

    set_objective(model, MIN_SENSE, sum(delta_P0[hvdc] for hvdc in set_of_hvdc))
    optimize!(model)
    results_dict["min_min"] = write_optimization_results(objective_value(model), delta_P0, delta_alpha,
                                                         network, set_of_hvdc, set_of_pst)

    for hvdc_optimized in set_of_hvdc
        set_objective(model, MIN_SENSE, delta_P0[hvdc_optimized] + 0.01*sum(delta_P0[hvdc] for hvdc in set_of_hvdc))
        optimize!(model)
        results_dict["min+_"*hvdc_optimized] = write_optimization_results(objective_value(model), delta_P0, delta_alpha,
                                                                          network, set_of_hvdc, set_of_pst)
        set_objective(model, MIN_SENSE, delta_P0[hvdc_optimized] - 0.01*sum(delta_P0[hvdc] for hvdc in set_of_hvdc))
        optimize!(model)
        results_dict["min-_"*hvdc_optimized] = write_optimization_results(objective_value(model), delta_P0, delta_alpha,
                                                                          network, set_of_hvdc, set_of_pst)

        set_objective(model, MAX_SENSE, delta_P0[hvdc_optimized] - 0.01*sum(delta_P0[hvdc] for hvdc in set_of_hvdc))
        optimize!(model)
        results_dict["max-_"*hvdc_optimized] = write_optimization_results(objective_value(model), delta_P0, delta_alpha,
                                                                          network, set_of_hvdc, set_of_pst)
        set_objective(model, MAX_SENSE, delta_P0[hvdc_optimized] + 0.01*sum(delta_P0[hvdc] for hvdc in set_of_hvdc))
        optimize!(model)
        results_dict["max+_"*hvdc_optimized] = write_optimization_results(objective_value(model), delta_P0, delta_alpha,
                                                                          network, set_of_hvdc, set_of_pst)
    end

    unfix(minimum_margin)
    set_objective(model, MAX_SENSE, minimum_margin)
    optimize!(model)
    results_dict["maximum_margin"] = Dict("objective_value" => objective_value(model),
                                          "P0" => Dict(hvdc => network._hvdcs[hvdc].elemP0+value(delta_P0[hvdc]) for hvdc in set_of_hvdc),
                                          "alpha0" => Dict(pst => network._psts[pst].alpha0+value(delta_alpha[pst]) for pst in set_of_pst))

    marging_P0_value = Dict(hvdc =>network._hvdcs[hvdc].elemP0+value(delta_P0[hvdc]) for hvdc in set_of_hvdc)
    marging_alpha0_value = Dict(pst => network._psts[pst].alpha0+value(delta_alpha[pst]) for pst in set_of_pst)

    println("\nThe HVDC setpoints maximizing the margins are : ", marging_P0_value)
    println("Which corresponds to PST setpoints : ", marging_alpha0_value)
    println("And the minimum_margin is (if negative this is an issue) ", value(minimum_margin))

    if isfile(results_file_name)
        all_results = JSON.parsefile(results_file_name)
    else
        all_results = Dict()
    end
    all_results[file_name] = results_dict
    open(results_file_name, "w") do file
        JSON.print(file, all_results, 2)
    end
    open("debug.json", "w") do debug_file
        all_flows = write_calculated_line_currents(delta_P0, delta_alpha, network, set_of_hvdc,
                                                   set_of_pst, set_of_quad_inc, dict_of_quad_inc_sensi)
        JSON.print(debug_file, all_flows, 2)
    end
end

# launch_optimization("all_data.json")
