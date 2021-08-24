import numpy as np
import sequence_jacobian as sj
from sequence_jacobian import het, simple, hetoutput, combine, solved, create_model, get_H_U
from sequence_jacobian.jacobian.classes import JacobianDict, ZeroMatrix
from sequence_jacobian.blocks.support.impulse import ImpulseDict


'''Part 1: Household block'''


def household_init(a_grid, y, rpost, sigma):
    c = np.maximum(1e-8, y[:, np.newaxis] +
                   np.maximum(rpost, 0.04) * a_grid[np.newaxis, :])
    Va = (1 + rpost) * (c ** (-sigma))
    return Va


@het(exogenous='Pi', policy='a', backward='Va', backward_init=household_init)
def household(Va_p, Pi_p, a_grid, y, rpost, beta, sigma):
    """
    Backward step in simple incomplete market model. Assumes CRRA utility.
    Parameters
    ----------
    Va_p    : array (E, A), marginal value of assets tomorrow (backward iterator)
    Pi_p    : array (E, E), Markov matrix for skills tomorrow
    a_grid  : array (A), asset grid
    y       : array (E), non-financial income
    rpost   : scalar, ex-post return on assets
    beta    : scalar, discount factor
    sigma   : scalar, utility parameter
    Returns
    -------
    Va    : array (E, A), marginal value of assets today
    a     : array (E, A), asset policy today
    c     : array (E, A), consumption policy today
    """
    uc_nextgrid = (beta * Pi_p) @ Va_p
    c_nextgrid = uc_nextgrid ** (-1 / sigma)
    coh = (1 + rpost) * a_grid[np.newaxis, :] + y[:, np.newaxis]
    a = sj.utilities.interpolate.interpolate_y(c_nextgrid + a_grid, coh, a_grid)  # (x, xq, y)
    sj.utilities.optimized_routines.setmin(a, a_grid[0])
    c = coh - a
    uc = c ** (-sigma)
    Va = (1 + rpost) * uc

    return Va, a, c


def get_mpcs(c, a, a_grid, rpost):
    """Approximate mpc, with symmetric differences where possible, exactly setting mpc=1 for constrained agents."""
    mpcs_ = np.empty_like(c)
    post_return = (1 + rpost) * a_grid

    # symmetric differences away from boundaries
    mpcs_[:, 1:-1] = (c[:, 2:] - c[:, 0:-2]) / \
        (post_return[2:] - post_return[:-2])

    # asymmetric first differences at boundaries
    mpcs_[:, 0] = (c[:, 1] - c[:, 0]) / (post_return[1] - post_return[0])
    mpcs_[:, -1] = (c[:, -1] - c[:, -2]) / (post_return[-1] - post_return[-2])

    # special case of constrained
    mpcs_[a == a_grid[0]] = 1

    return mpcs_


def income(tau, Y, e_grid, e_dist, Gamma, transfer):
    """Labor income on the grid."""
    gamma = e_grid ** (Gamma * np.log(Y)) / np.vdot(e_dist,
                                                    e_grid ** (1 + Gamma * np.log(Y)))
    y = (1 - tau) * Y * gamma * e_grid + transfer
    return y


@simple
def income_state_vars(rho_e, sd_e, nE):
    e_grid, e_dist, Pi = sj.utilities.discretize.markov_rouwenhorst(
        rho=rho_e, sigma=sd_e, N=nE)
    return e_grid, e_dist, Pi


@simple
def asset_state_vars(amin, amax, nA):
    a_grid = sj.utilities.discretize.agrid(amin=amin, amax=amax, n=nA)
    return a_grid


@hetoutput()
def mpcs(c, a, a_grid, rpost):
    """MPC out of lump-sum transfer."""
    mpc = get_mpcs(c, a, a_grid, rpost)
    return mpc


household.add_hetinput(income, verbose=False)
household.add_hetoutput(mpcs, verbose=False)


'''Part 2: rest of the model'''


@simple
def interest_rates(r):
    rpost = r(-1)  # household ex-post return
    rb = r(-1)     # rate on 1-period real bonds
    return rpost, rb


@simple
def fiscal(B, G, rb, Y, transfer):
    rev = rb * B + G + transfer   # revenue to be raised
    tau = rev / Y
    return rev, tau

# @simple
# def fiscal(B, G, rb, Y, transfer, rho_B):
#     B_rule = B.ss + rho_B * (B(-1) - B.ss + G - G.ss) - B
#     rev = (1 + rb) * B(-1) + G + transfer - B   # revenue to be raised
#     tau = rev / Y
#     return B_rule, rev, tau


@solved(unknowns={'B': (0.0, 10.0)}, targets=['B_rule'], solver='brentq')
def fiscal_solved(B, G, rb, Y, transfer, rho_B):
    B_rule = B.ss + rho_B * (B(-1) - B.ss + G - G.ss) - B
    rev = (1 + rb) * B(-1) + G + transfer - B   # revenue to be raised
    tau = rev / Y
    return B_rule, rev, tau


@simple
def mkt_clearing(A, B, C, Y, G):
    asset_mkt = A - B
    goods_mkt = Y - C - G
    return asset_mkt, goods_mkt


'''Try this'''

# DAG with a nested CombinedBlock 
hh = combine([household, income_state_vars, asset_state_vars], name='HH')
dag = sj.create_model([hh, interest_rates, fiscal_solved, mkt_clearing], name='HANK')

# Calibrate steady state
calibration = {'Y': 1.0, 'r': 0.005, 'sigma': 2.0, 'rho_e': 0.91, 'sd_e': 0.92, 'nE': 3,
               'amin': 0.0, 'amax': 1000, 'nA': 100, 'Gamma': 0.0, 'transfer': 0.143, 'rho_B': 0.8}

ss = dag.solve_steady_state(calibration, dissolve=['fiscal_solved'], solver='hybr',
                            unknowns={'beta': .95, 'G': 0.2, 'B': 2.0},
                            targets={'asset_mkt': 0.0, 'tau': 0.334, 'Mpc': 0.25})

# ss0 = dag.solve_steady_state(calibration, solver='hybr',
#                             unknowns={'beta': .95, 'G': 0.2, 'B': 2.0},
#                             targets={'asset_mkt': 0.0, 'tau': 0.334, 'Mpc': 0.25})
# assert all(np.allclose(ss0[k], ss[k]) for k in ss0)

# Precompute household Jacobian
Js = {'household': household.jacobian(ss, inputs=['Y', 'rpost', 'tau', 'transfer'], outputs=['C', 'A'], T=300)}

# Solve Jacobian
G = dag.solve_jacobian(ss, inputs=['r'], outputs=['Y', 'C', 'asset_mkt', 'goods_mkt'],
                       unknowns=['Y'], targets=['asset_mkt'], T=300, Js=Js)
shock = ImpulseDict({'r': 1E-4*0.9**np.arange(300)})
td_lin1 = G @ shock 

# Compare to solve_impulse_linear
td_lin2 = dag.solve_impulse_linear(ss, unknowns=['Y'], targets=['asset_mkt'],
                                   inputs=shock, outputs=['Y', 'C', 'asset_mkt', 'goods_mkt'], Js=Js)
assert all(np.allclose(td_lin1[k], td_lin2[k]) for k in td_lin1)

# Solve impulse_nonlinear
td_hh = hh.impulse_nonlinear(ss, td_lin1[['Y']], outputs=['C', 'A'], Js=Js) 

td_nonlin = dag.solve_impulse_nonlinear(ss, unknowns=['Y'], targets=['asset_mkt'],
                                        inputs=shock, outputs=['Y', 'C', 'asset_mkt', 'goods_mkt'], Js=Js)
