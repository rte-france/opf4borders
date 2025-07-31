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
_BRANCH = "ac_line"
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
    for (branchOrHvdc, v1) in json[_SENSI], (branch, v2) in v1, (INC,v3) in v2, (element,v4) in v3 
        network._sensi[branch,  INC, element] = v4
    end
    return network
end


function create_model(quiet::Bool, network::NETWORK, set_of_hvdc::Base.KeySet, set_of_pst::Set,
                      set_of_quad_inc::Set, dict_of_quad_inc_sensi::Dict)
    model = Model( Xpress.Optimizer)
    MOI.set(model, MOI.Silent(), quiet)

    @variables(model,
    begin
        network._hvdcs[hvdc].pMin -  network._hvdcs[hvdc].elemP0 <=
            delta_P0[hvdc in set_of_hvdc] <=
            network._hvdcs[hvdc].pMax - network._hvdcs[hvdc].elemP0
    end);

    @objective(model, MIN_SENSE, sum(delta_P0[hvdc] for hvdc in set_of_hvdc))


    @constraints(model,
    begin
        Current_Max_Pos[(quad, inc) in  set_of_quad_inc], 
        network._sensi[quad, inc, _REFERENCE_CURRENT] +
            sum(val * delta_P0[hvdc] for (hvdc, val) in dict_of_quad_inc_sensi[quad, inc]) <=
            + network._quads[quad].limits[_PERMANENT_LIMIT]
    end
    )

    @constraints(model, 
    begin
        Current_Max_Neg[(quad, inc) in  set_of_quad_inc], 
        network._sensi[quad, inc, _REFERENCE_CURRENT] +
            sum(val* delta_P0[hvdc] for (hvdc, val) in dict_of_quad_inc_sensi[quad, inc]) >=
            -network._quads[quad].limits[_PERMANENT_LIMIT]
    end
    )
    return model, delta_P0
end

function launch_optimization(file_name::String)
    launch_optimization(file_name, "results.json")    
end

function launch_optimization(file_name::String, results_file_name::String)
    network = read_json(file_name)
    set_of_hvdc = keys(network._hvdcs);
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

    model, delta_P0 = create_model(true, network, set_of_hvdc, Set(), set_of_quad_inc, dict_of_quad_inc_sensi)

    set_objective(model, MIN_SENSE, sum(delta_P0[hvdc] for hvdc in set_of_hvdc))
    optimize!(model)
    min_value = objective_value(model)
    min_P0_value = Dict(hvdc => network._hvdcs[hvdc].elemP0+value(delta_P0[hvdc]) for hvdc in set_of_hvdc)

    set_objective(model, MAX_SENSE, sum(delta_P0[hvdc] for hvdc in set_of_hvdc))
    optimize!(model)
    max_value = objective_value(model)
    max_P0_value = Dict(hvdc => network._hvdcs[hvdc].elemP0+value(delta_P0[hvdc]) for hvdc in set_of_hvdc)

    middle_P0_value = Dict(hvdc => 0.5*(min_P0_value[hvdc]+max_P0_value[hvdc]) for hvdc in set_of_hvdc)
    set_objective(model, MIN_SENSE, sum((delta_P0[hvdc] + network._hvdcs[hvdc].elemP0 - middle_P0_value[hvdc])^2 for hvdc in set_of_hvdc))
    optimize!(model)

    marging_P0_value = Dict(hvdc =>network._hvdcs[hvdc].elemP0+value(delta_P0[hvdc]) for hvdc in set_of_hvdc)

    println("The min safe setpoints are : ", min_P0_value)
    println("The max safe setpoints are : ", max_P0_value)
    println("The safest setpoints are : ", middle_P0_value)
    println("The ", marging_P0_value)

    for hvdc in set_of_hvdc
        println( value(delta_P0[hvdc] + network._hvdcs[hvdc].elemP0 - middle_P0_value[hvdc]) )
    end

    if isfile(results_file_name)
        all_results = JSON.parsefile(results_file_name)
    else
        all_results = Dict()
    end
    all_results[file_name] = Dict("Min safe points" => min_P0_value,
                                  "Max safe points" => max_P0_value,
                                  "Safest points" => middle_P0_value)
    open(results_file_name, "w") do file
        JSON.print(file, all_results, 2)
    end
end

# launch_optimization("all_data.json")
