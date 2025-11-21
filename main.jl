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
using PrettyPrint

include("read_inputs.jl")
include("model.jl")

function write_optimization_results(objective_val::Float64, delta_P0::JuMP.Containers.DenseAxisArray, delta_alpha::JuMP.Containers.DenseAxisArray,
                                    network::NETWORK, set_of_hvdc::Set, set_of_pst::Set)
    P0_value = Dict(hvdc => network._hvdcs[hvdc].elemP0+value(delta_P0[hvdc]) for hvdc in set_of_hvdc)
    alpha0_value = Dict(pst => network._psts[pst].alpha0+value(delta_alpha[pst]) for pst in set_of_pst)
    return Dict("objective_value" => objective_val,
                "P0" => P0_value,
                "alpha0" => alpha0_value)    
end

function write_calculated_line_currents(delta_P0::JuMP.Containers.DenseAxisArray, delta_alpha::JuMP.Containers.DenseAxisArray,
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
    
    available_counter = Set(keys(network._countertrading))
    for counter in available_counter
        if network._countertrading[counter].pMax == 0 & network._countertrading[counter].pMin ==0
            delete!(available_counter, counter)
        end
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

    model, delta_P0, delta_alpha, minimum_margin, current_slack = #, hvdc_slack =
        create_model(true, network, set_of_hvdc, set_of_pst, available_counter, 
                     set_of_quad_inc, set_of_hvdc_inc, dict_of_quad_inc_sensi, 1)

    optimize!(model)
    minimum_margin_possible = value(minimum_margin)
    println("The maximum margin possible is (if negative this is an issue) ", minimum_margin_possible)

    if minimum_margin_possible > 0
        # possible to satisfy all the constraints
        fix(minimum_margin, 0.0)
    else
        problematic_contigencies = Set()
        problematic_contigencies_overloads = Dict()
        for (quad, inc) in set_of_quad_inc
            if value(current_slack[(quad, inc)]) < 0
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
            println("Contingency is ", contingency)
            problematic_quad_inc = filter(couple-> (couple[2]==contingency), set_of_quad_inc)
            set_objective(model, MAX_SENSE,
                          sum(current_slack[(quad, cont)] for (quad, cont) in problematic_quad_inc))
            optimize!(model)
            println("\nThe HVDC setpoints maximizing the margins for $contingency contingency is: ",
                    Dict(hvdc => network._hvdcs[hvdc].elemP0+value(delta_P0[hvdc]) for hvdc in set_of_hvdc))
            println("Which corresponds to PST setpoints : ", 
                    Dict(pst => network._psts[pst].alpha0+value(delta_alpha[pst]) for pst in set_of_pst))
            println("The margin (on problematic lines) is then: ", objective_value(model))
            # cur_slack = Dict((quad, cont) => value(current_slack[(quad, cont)]) for (quad, cont) in problematic_quad_inc)
            # println("Negative values of slack: ", Dict((quad,cont) => cur_slack[(quad,cont)]
            #                             for (quad, cont) in problematic_contigencies if cur_slack[(quad,cont)] < 0))
        end
        for (quad, inc) in set_of_quad_inc
            if ! haskey(problematic_contigencies_overloads, inc) || ! (quad in problematic_contigencies_overloads[inc])
                fix(current_slack[(quad, inc)], 0.0)
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
