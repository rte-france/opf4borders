using CairoMakie
using JSON
using Statistics

include("main.jl")


function plot_results(data_key::String)
    plot_results("results.json", data_key)
end

function plot_results(file_name::String, data_key::String)
    """Plot the safe region for two HVDCs
    - data_key: name of the situation to plot in the result_json file (first level of json keys),
    ie the name of the json data file used as entry for the optimization"""
    if isfile(file_name)
        all_results = JSON.parsefile(file_name)
        network = read_json(data_key)
    else
        exit()
    end
    results_dict = all_results[data_key]
    
    extremal_points = Point2f[]
    hvdc1_name, hvdc2_name = keys(network._hvdcs)

    max_margin = Point2f(0,0)
    reference_point = Point2f(0,0)
    
    for (calculation_type, calculation_result) in results_dict
        setpoints = calculation_result["P0"]
        if calculation_type == "maximum_margin"
            max_margin = Point2f(setpoints[hvdc1_name][_BASECASE], setpoints[hvdc2_name][_BASECASE])
        elseif calculation_type == "reference"
            reference_point = Point2f(setpoints[hvdc1_name], setpoints[hvdc2_name])
        else
            push!(extremal_points, Point2f(setpoints[hvdc1_name][_BASECASE], setpoints[hvdc2_name][_BASECASE]))
        end
    end
    
    # Sorting points by increasing angle to the center point for a proper plot
    mean_point = mean(extremal_points)
    sort!(extremal_points, by = x -> atan(x[2]-mean_point[2], x[1]-mean_point[1]))
    
    f = Figure()
    ax = Axis(f[1, 1],
    title = "Safe setpoints for the HVDCs on "*data_key,
    xlabel = "Setpoint of "*hvdc1_name*" (MW)",
    ylabel = "Setpoint of "*hvdc2_name*" (MW)",
    aspect = DataAspect()
    )

    min_hvdc1 = network._hvdcs[hvdc1_name].pMin
    min_hvdc2 = network._hvdcs[hvdc2_name].pMin
    max_hvdc1 = network._hvdcs[hvdc1_name].pMax
    max_hvdc2 = network._hvdcs[hvdc2_name].pMax
    poly!(Point2f[(min_hvdc1,min_hvdc2), (min_hvdc1,max_hvdc2),
                  (max_hvdc1,max_hvdc2), (max_hvdc1,min_hvdc2)], linestyle= :dash,
          color = :white, strokecolor = :black, strokewidth = 1, label="Setpoint limits")

    poly!(extremal_points, color = :red, strokecolor = :black, strokewidth = 1, label="Safe area")
    scatter!(max_margin, color= :blue, label="Setpoints maximizing the margins")
    scatter!(reference_point, color= :black, label="Initial setpoints")
    
    f[1, 2] = Legend(f, ax, framevisible = false, labelsize = 10.0f0)
    f
    save(split(data_key,".")[1]*".png", f)
end
