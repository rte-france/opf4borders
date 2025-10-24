# Equations of the linearized AC-OPF

This Readme aims at describing precisely the model used in the julia OPF code.

The aim of the model is to characterize the safest safepoints of a few levers that are at the hands of the TSO, in order to optimally operate an interconnection with HVDC links. Hence, the "optimal" part of the powerflow is very restricted, for the moment to HVDC (High Voltage Direct Current lines) and PST (Phase-Shifter Transformers) setpoints.

# Generalities on the model
The model implements a linearized AC-OPF, "around a network state", with the following philosophy.

Let's $\mathcal{L}, \mathcal{H}, \mathcal{P}$ be the sets (respectively) of monitores lines, controllable HVDCs and PSTs.
Let's $I, P$ designates the current and the power, the upper/superscripts will indicate whether we're speaking of injection, flow on lines,... and $\alpha$ designates the phase-shift of a transformer.

Let's say you have determined some initial state of the network, denoted by ${}_0$ on the variables. It should be the result of an AC-loadflow, in order for the state to be coherent.

The idea is to calculate the sensitivities of all monitored lines $\ell \in\mathcal{L}$, with respect to a variation of the setpoint of each controllable element $h \in \mathcal{H}, p \in \mathcal{P} $ (which corresponds to elements of the Jacobian matrix of the network on the $0$ state): 

$$\left.\frac{\partial I^\ell}{\partial P^h}\right|_0 ; \left.\frac{\partial I^\ell}{\partial \alpha^p}\right|_0$$

# Equations of the linearized AC-OPF
## Setpoints of the variable elements
As we are "moving around" the $0$ state, the setpoints $x \in\{P,\alpha\}$ of an element $y\in\mathcal{H} \cup \mathcal{P}$ will be define as a variation around its initial value :
$$x^y = x_0^y + \Delta x^y$$
and the optimisation variable will be $\Delta x^y$.

## "Hybrid" control of the HVDC

The HVDCs are controlled using an hybrid mode, the setpoint $P^h$ is defined as

$$P^h = P^{h,c} + P^{h,v} = P^{h,c} + k^h\Delta\theta^h$$ 

where $P^{h,c}$ is the HVDC constant setpoint (which will be optimized), $k^h$ is a (predefined gain) and $\Delta\theta^h$ is the difference between electrical angles on both sides of the HVDC.

This control is implemented by modelising the HVDC as two (three) distinct element:
1. An HVDC $h^c$ whose power flow would be the constant setpoint $P^{h,c}$, which in fact corresponds to an injection of $P^{h,c}$ at the end node and of $-P^{h,c}$ at the origin node.
2. An equivalent AC line $h^\mathrm{eq}$, of impedance $X= 1/k$

Similarly to the AC-lines, we can compute the sensitivities of the equivalent AC-lines to the variation of setpoints of the controllable elements. This will capture the impact on $\Delta\theta^h$ of the variation setpoints, hence the impact on the real setpoint of the HVDC. 

Note, that changing $P^{h,c}$ has an impact on the voltage angles of the nodes, so it has an impact on $\Delta\theta^h$, so it changes the setpoint $P^{h}$, which in turns will change the angles. However, the sensitivities calculated will already take into account this feedback, so the setpoint is a function of the variation of $P^{h,c}$, not $P^h$.

The total setpoint of the HVDC is then given by:

$$P^h = P^h_0 + \Delta P^h $$

$$P^h = \underbrace{P_0^h + k^h\Delta\theta^h_0}_\text{Setpoint of the initial state}$$

$$+\underbrace{\Delta P^{h,c}}_\text{Optimization variable} $$ 

$$+ \sum_{h'\in\mathcal{H}} \left.\frac{\partial P^{h^\mathrm{eq}}}{\partial P^{h'}}\right|_0\Delta P^{h',c} $$

$$+ \sum_{p\in\mathcal{P}} \left.\frac{\partial P^{h^\mathrm{eq}}}{\partial \alpha^p}\right|_0\Delta\alpha^p$$


## Flow on a AC-line

The flow on an AC-line $\ell\in\mathcal{L}^\mathrm{AC}$ is defined affinely as a function of the setpoints:

$$I^\ell = I_0^\ell + \sum_{h\in\mathcal{H}} \left.\frac{\partial I^\ell}{\partial P^{h}}\right|_0\Delta P^{h,c}$$

$$+ \sum_{p\in\mathcal{P}} \left.\frac{\partial I^\ell}{\partial \alpha^p}\right|_0\Delta\alpha^p$$

To be more precise, the sensitivity of an HVDC on a line corresponds to the 1. of the modelization of the HVDC, which means that it simply the sum of the sensitivity at the connecting nodes of the HVDC on the given line:

$$\frac{\partial I^\ell}{\partial P^{h}} = \frac{\partial I^\ell}{\partial P^\mathrm{end}} - \frac{\partial I^\ell}{\partial P^\mathrm{or}}$$

where $P^\mathrm{end}, P^\mathrm{or}$ is the power injection at the (respectively) $\mathrm{end}$ and $\mathrm{origin}$ nodes of the HVDC.

## Line limits
The flow on a monitored line $\ell$ must not exceed the allowed limit $I^{\ell,\max}$:

$$-I^{\ell,\max}\leqslant I^\ell \leqslant I^{\ell,\max}$$

And similarly for the HVDCs:
$$-P^{h,\max}\leqslant P^h \leqslant P^{h,\max}$$

Note that the behaviour of the HVDC when the setpoint is saturated at its limit ($P^h = P^{h,\max}$ with no more variable part) is not implemented in the model yet, which forces the setpoint to be far enough from the limits ($P^h_c=P^{h,\max}$ and $P^h_c=0$ is not possible in this model).

## Contingencies
In the current model, the N-1 rule is implemented taking into account no curative actions, hence we consider that the setpoints decided for the N state must be safe for all the contingencies.

In reality, for each contingency, you need to calculate the sensitivities accordingly, *ie* do an AC-loadflow post contingency and calculate a set of sensitivities on that network state. Then, all the variables written above are defined for each contingency (aswell as the basecase state) and the constraints must be verified for each contingency.

# Optimization workflow
## Relaxed constraints
To ensure the feasiblility of the optimization problem, the limit constraints are relaxed with a slack variable:

$$\forall c, I^\ell_{c} \leqslant I^{\ell,\max} - \epsilon^\ell_{c,+}$$
$$\forall c, I^\ell_{c} \geqslant - I^{\ell,\max} + \epsilon^\ell_{c,-}$$

If $\epsilon$ is positive, the limit is satisfied, hence we define the minimum margin $m$ as:

$$ \forall c, m \leqslant \min(\epsilon^\ell_{c,+},\epsilon^\ell_{c,-}) $$

## Feasibility of the optimization
The first optimization determines whether a safe state (both in basecase and for all N-1), by maximizing the minimum margin $m$.

$$ \max m $$

If $m$ is negative, at least one contingency creates a constraint that a common preventive optimization cannot solve. 

## Optimization of the problematic N-1 (if $m<0$)
If it is the case, an optimization problem tries to solve separately the problematic cases.
From the previous optimization problem, we get those cases defined by 
$$ \{ c | \epsilon^\ell_{c,+} < 0 \perp \epsilon^\ell_{c,-} < 0 \} $$

For all those unsolved $c^u$, we check if some (curative) setpoints could solve the issue:

$$\max \sum_\ell (\epsilon^\ell_{c^u,+} + \epsilon^\ell_{c^u,-})$$

## Optimization without the problematic N-1
Then, a bunch of optimization is launched, to define a safe set of HVDC setpoints. The idea is to maximize/minimize linear combination of the setpoints, which defines the edges of the safe state.

$$ \max \sum_h \pm \Delta P^{h,c} $$

If $m$ was positive, all contigencies are kept (we fix $\epsilon = 0$ to make the constraint hard). If not, the problematic N-1 are dropped (we fix only the slacks for the feasible N-1, the problematic ones are free).
