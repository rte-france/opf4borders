using CairoMakie
using JSON
using Statistics


function plot_results(data_key::String)
    plot_results("results.json", data_key)
end

function plot_results(file_name::String, data_key::String)
    """Plot the safe region for two HVDCs
    - data_key: name of the situation to plot in the result_json file (first level of json keys),
    ie the name of the json data file used as entry for the optimization"""
    if isfile(file_name)
        all_results = JSON.parsefile(file_name)
    else
        exit()
    end
    results_dict = all_results[data_key]
    
    extremal_points = Point2f[]
    hvdc1_name = ""
    hvdc2_name = ""
    max_margin = Point2f(0,0)
    reference_point = Point2f(0,0)
    
    for (calculation_type, calculation_result) in results_dict
        setpoints = calculation_result["P0"]
        if isempty(hvdc1_name)
            (hvdc1_name, hvdc2_name) = keys(setpoints)
        end
        if calculation_type == "maximum_margin"
            max_margin = Point2f(setpoints[hvdc1_name], setpoints[hvdc2_name])
        elseif calculation_type == "reference"
            reference_point = Point2f(setpoints[hvdc1_name], setpoints[hvdc2_name])
        else
            push!(extremal_points, Point2f(setpoints[hvdc1_name], setpoints[hvdc2_name]))
        end
    end
    
    # Sorting points by increasing angle to the center point for a proper plot
    mean_point = mean(extremal_points)
    sort!(extremal_points, by = x -> atan(x[2]-mean_point[2], x[1]-mean_point[1]))
    
    f = Figure(size=(500,450))
    ax = Axis(f[1, 1],
    title = "Safe setpoints for the HVDCs on "*data_key,
    xlabel = "Setpoint of "*hvdc1_name*" (MW)",
    ylabel = "Setpoint of "*hvdc2_name*" (MW)",
    aspect = DataAspect()
    )
    
    
    poly!(Point2f[(-2000,-2000), (-2000,2000), (2000,2000), (2000,-2000)], linestyle= :dash,
    color = :white, strokecolor = :black, strokewidth = 1, label="Setpoint limits")
    poly!(extremal_points, color = :red, strokecolor = :black, strokewidth = 1, label="Safe area")
    scatter!(max_margin, color= :blue, label="Setpoints maximizing the margins")
    scatter!(reference_point, color= :black, label="Initial setpoints")
    
    axislegend(position = :rb)
    f
    save(split(data_key,".")[1]*".png", f)
end

plot_results("all_data_5011_withPST.json")
