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
    # (sensi, branch, INC, element) ==> value
    for (branch, v2) in json[_SENSI][_BRANCH], (INC,v3) in v2, (element,v4) in v3
        network._sensi[branch,  INC, element] = v4
    end
    return network
end


function create_model(quiet::Bool, network::NETWORK, set_of_hvdc::Set, set_of_pst::Set,
                      set_of_quad_inc::Set, dict_of_quad_inc_sensi::Dict)
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

    @variable(model, minimum_margin)

    @objective(model, MIN_SENSE, sum(delta_P0[hvdc] for hvdc in set_of_hvdc))


    @constraints(model,
    begin
        Current_Max_Pos[(quad, inc) in set_of_quad_inc],
        minimum_margin <= network._quads[quad].limits[_PERMANENT_LIMIT] -
            (network._sensi[quad, inc, _REFERENCE_CURRENT] +
            sum(val * delta_alpha[pst] for (pst, val) in dict_of_quad_inc_sensi[quad, inc] if pst in set_of_pst) +
            sum(val * delta_P0[hvdc] for (hvdc, val) in dict_of_quad_inc_sensi[quad, inc] if hvdc in set_of_hvdc))
    end
    )

    @constraints(model,
    begin
        Current_Max_Neg[(quad, inc) in  set_of_quad_inc],
        minimum_margin <= network._quads[quad].limits[_PERMANENT_LIMIT] +
            network._sensi[quad, inc, _REFERENCE_CURRENT] +
            sum(val * delta_alpha[pst] for (pst, val) in dict_of_quad_inc_sensi[quad, inc] if pst in set_of_pst) +
            sum(val * delta_P0[hvdc] for (hvdc, val) in dict_of_quad_inc_sensi[quad, inc] if hvdc in set_of_hvdc)
    end
    )

    @constraint(model, minimum_margin >= 0)
    return model, delta_P0, delta_alpha, minimum_margin
end

function launch_optimization(file_name::String)
    launch_optimization(file_name, "results.json")
end

function  write_optimization_results(objective_val::Float64, delta_P0::JuMP.Containers.DenseAxisArray, delta_alpha::JuMP.Containers.DenseAxisArray,
                                     network::NETWORK, set_of_hvdc::Set, set_of_pst::Set)
    P0_value = Dict(hvdc => network._hvdcs[hvdc].elemP0+value(delta_P0[hvdc]) for hvdc in set_of_hvdc)
    alpha0_value = Dict(pst => network._psts[pst].alpha0+value(delta_alpha[pst]) for pst in set_of_pst)
    return Dict("objective_value" => objective_val,
                "P0" => P0_value,
                "alpha0" => alpha0_value)    
end

function launch_optimization(file_name::String, results_file_name::String)
    network = read_json(file_name)
    set_of_hvdc = Set(keys(network._hvdcs));
    set_of_pst = Set(keys(network._psts));
    set_of_quad_inc = Set();
    dict_of_quad_inc_sensi = Dict()

    for (quad, inc, element) in keys(network._sensi)
        if haskey(network._quads, quad) && ! haskey(dict_of_quad_inc_sensi, (quad, inc))
            push!(set_of_quad_inc, (quad, inc))
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

    model, delta_P0, delta_alpha, minimum_margin = create_model(true, network, set_of_hvdc, set_of_pst,
                                                                set_of_quad_inc, dict_of_quad_inc_sensi)

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
end

# launch_optimization("all_data.json")
