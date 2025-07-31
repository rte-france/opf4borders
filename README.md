# topase.jl

# Description

This module is an Operational Research tool that implements an OPF aiming at optimize the setpoint of one or several HVDC lines, while ensuring safety constraints (line limits, N-1 rule, ...).

The setpoints of Phase Shift Transformers can also be optimized (optionnal).

# Requirements

This module works with Julia, v1.11 and the Xpress solver.

You will need the following packages in your Julia environment : `JSON, JuMP, Xpress, PrettyPrint` that you can install with the following:

```bash
julia --project=.
]
instantiate
```

# Minimal working example
The function launching the optimization from a json data file is `launch_optimization`. With the `example.json` file, the optimization can be launched as follows (provided you have installed Xpress on your computer).

```bash
julia --project=.
include("main.jl")
launch_optimization("example.json")
```

# Data
## File structure

All the data needed for the calculation is stored in one json file, that has the following structure :

```json
{
    "sensi" : {
        "ac_line" : {
            "LINE_NAME": {
                "CONTINGENCY_i" : {
                    "ref_current" : value,
                    "ELEMVAR_j" : sensi_value
                }
            }
        },
        "hvdc_line" : {
            "HVDC_NAME": {
                "CONTINGENCY_i" : {
                    "ref_current" : value,
                    "ELEMVAR_j" : sensi_value
                }
            }
        }
    },
    "quads" : {
        "LINE_NAME" : {
            "LIMIT_NAME" : value,
            ...
        }
    }, 
    "elemVars" : {
        "hvdc" : {
            "HVDC_NAME" : {
                "min" : value,
                "max" : value,
                "ref_setpoint" : value
            }
        },
        "pst" : {
            "PST_NAME" : {
                "min" : value,
                "max" : value,
                "ref_setpoint" : value
            }
        }
    }
}
```

All values are floats (ie not between quotes). String that are in uppercase are supposed to be replaced by names, while the lowercase one are the precise key names.

## Detailled explanation
All the keys and values are explained:
- **sensi** : a dictionnary containing all sensitivity information, ie impacts of a setpoint on the current of an AC line (or the powerflow) of a DC line in AC emulation. Its keys are:
    -
    - **ac_line** : a dictionnary containing information on the sensitivity of the AC lines
        - **name of the line** : a dictionnary that has the information of the sensitivity for all variants (N and all N-k)
            - **name of the variant** (**BASECASE** for the basecase, or **name of the line in default** for an N-1) : dict of sensitivities
                - **ref_current** : current with nominal case (here nominal will mean: the setpoints are the default ones)
                - **name of the variable element** (HVDC or PST) : value of the sensitivity in A/MW or A/rad
    - **hvdc_line** : a dictionnary containing information on the sensitivity of the HVDC lines (for the ones in AC emulation). Same keys as above, only the units will change :
                - **name of the variable element** (HVDC or PST) : value of the sensitivity in MW/MW or MW/rad
- **quads** : a dictionnary defining the line current limits
    -
    - **name of the line** : a dictionnary of current limits for the given line
        - **limit_name** : value (in A). The limit name should be *permanent_limit* to use the code as it is (07/2025). Other limits could be defined (*eg*, temporary limits to be used in post-contingency case)
- **elemVars** : a dictionnary defining the network elements whose setpoints will be optimized
    -
    - **hvdc** : a dictionnary defining the optimized HVDCs
        - **hvdc name** : defines values for the given HVDC
            - **ref_setpoint** : setpoint of the HVDC in the network state used for the calculation of the sensitivities
            - **min** : minimal setpoint
            - **max** : maximal setpoint
    - **pst** : a dictionnary defining the optimized PSTs
        - **pst name** : defines values for the given PST (for the moment, we consider continuous setpoints)
            - **ref_setpoint** : setpoint of the PST in the network state used for the calculation of the sensitivities
            - **min** : minimal setpoint
            - **max** : maximal setpoint

The keys that are not ids of lines / HVDC / PST are defined at the top of the julia code, so changing the key name implies simply to adapt the string in the julia file accordingly.
