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


_BASECASE  = "N"
_SENSI = "sensitivities"
_BRANCH = "branch"
_QUADS = "quads"
_ELEMVARS="elemVars"
_PERMANENT_LIMIT = "permanent_limit"
_MIN = "min"
_MAX = "max"
_ELEMP0 = "referenceSetpoint"
_HVDC = "hvdc"
_PST = "pst"
_REFERENCE_CURRENT = "referenceCurrent"
_COUNTER_TRADING = "counterTrading"

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
    _countertrading::Dict{String, HVDC};

    _sensi::Dict{Tuple{String, String, String}, Float64};

    NETWORK() = new(Dict{String, HVDC}(), Dict{String, PST}(), Dict{String, QUAD}(), Dict{String, HVDC}(),
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

    for (hvdc_name, hvdc_info) in get(json[_ELEMVARS], _HVDC, Dict())
        network._hvdcs[hvdc_name] = HVDC(hvdc_name, hvdc_info[_MIN], hvdc_info[_MAX], hvdc_info[_ELEMP0])
    end
    for (pst_name, pst_info) in get(json[_ELEMVARS], _PST, Dict())
        network._psts[pst_name] = PST(pst_name, pst_info[_MIN], pst_info[_MAX], pst_info[_ELEMP0])
    end
    for (counter, counter_info) in get(json[_ELEMVARS], _COUNTER_TRADING, Dict())
        network._countertrading[counter] = HVDC(counter, counter_info[_MIN], counter_info[_MAX], 0)
        # Counter trading is defined compared to the initial situation, so it is 
        # necessarily at 0 on the input data
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
