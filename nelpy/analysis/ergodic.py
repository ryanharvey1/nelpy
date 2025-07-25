"""
:mod:`ergodic` --- summary measures for ergodic Markov chains
=============================================================
"""

__author__ = "Sergio J. Rey <srey@asu.edu> "

__all__ = ["steady_state", "fmpt"]

import numpy as np
import numpy.linalg as la


def set_self_transition_zero(x):
    """Set cost/length of self-transition to zero."""
    np.fill_diagonal(x, 0.0)


def steady_state(P):
    """
    Calculates the steady state probability vector for a regular Markov
    transition matrix P
    Parameters
    ----------
    P        : array (kxk)
               an ergodic Markov transition probability matrix
    Returns
    -------
    implicit : array (kx1)
               steady state distribution
    Examples
    --------
    Taken from Kemeny and Snell. [1]_ Land of Oz example where the states are
    Rain, Nice and Snow - so there is 25 percent chance that if it
    rained in Oz today, it will snow tomorrow, while if it snowed today in
    Oz there is a 50 percent chance of snow again tomorrow and a 25
    percent chance of a nice day (nice, like when the witch with the monkeys
    is melting).
    >>> import numpy as np
    >>> p = np.array([[0.5, 0.25, 0.25], [0.5, 0, 0.5], [0.25, 0.25, 0.5]])
    >>> steady_state(p)
    array([[ 0.4],
           [ 0.2],
           [ 0.4]])
    Thus, the long run distribution for Oz is to have 40 percent of the
    days classified as Rain, 20 percent as Nice, and 40 percent as Snow
    (states are mutually exclusive).
    """

    v, d = la.eig(np.transpose(P))

    # for a regular P maximum eigenvalue will be 1
    mv = max(v.real)  # Use real part for comparison
    # find its position
    i = v.real.tolist().index(mv)

    # normalize eigenvector corresponding to the eigenvalue 1
    # Take real part to avoid complex warning
    eigenvector = d[:, i].real
    return eigenvector / np.sum(eigenvector)


def fmpt(P):
    """
    Calculates the matrix of first mean passage times for an
    ergodic transition probability matrix.
    Parameters
    ----------
    P    : array (kxk)
           an ergodic Markov transition probability matrix
    Returns
    -------
    M    : array (kxk)
           elements are the expected value for the number of intervals
           required for  a chain starting in state i to first enter state j
           If i=j then this is the recurrence time.

    Examples
    --------
    >>> import numpy as np
    >>> p = np.array([[0.5, 0.25, 0.25], [0.5, 0, 0.5], [0.25, 0.25, 0.5]])
    >>> fm = fmpt(p)
    >>> fm
    array([[ 2.5       ,  4.        ,  3.33333333],
           [ 2.66666667,  5.        ,  2.66666667],
           [ 3.33333333,  4.        ,  2.5       ]])
    Thus, if it is raining today in Oz we can expect a nice day to come
    along in another 4 days, on average, and snow to hit in 3.33 days. We can
    expect another rainy day in 2.5 days. If it is nice today in Oz, we would
    experience a change in the weather (either rain or snow) in 2.67 days from
    today. (That wicked witch can only die once so I reckon that is the
    ultimate absorbing state).

    Notes -----
    Uses formulation (and examples on p. 218) in Kemeny and Snell (1976) [1]_
    References
    ----------

    .. [1] Kemeny, John, G. and J. Laurie Snell (1976) Finite Markov
       Chains. Springer-Verlag. Berlin
    """
    P = np.asarray(P)
    A = np.zeros_like(P)
    ss = steady_state(P)
    k = ss.shape[0]
    for i in range(k):
        A[:, i] = ss.flatten()
    A = A.T
    identity_matrix = np.identity(k)
    Z = la.inv(identity_matrix - P + A)
    E = np.ones_like(Z)
    D = np.diag(1.0 / np.diag(A))
    Zdg = np.diag(np.diag(Z))
    M = (identity_matrix - Z + np.multiply(E, Zdg)) @ D
    return M


def var_fmpt(P):
    """
    Variances of first mean passage times for an ergodic transition
    probability matrix
    Parameters
    ----------
    P    : array (kxk)
           an ergodic Markov transition probability matrix
    Returns
    -------
    implic : array (kxk)
             elements are the variances for the number of intervals
             required for  a chain starting in state i to first enter state j
    Examples
    --------
    >>> import numpy as np
    >>> p = np.array([[0.5, 0.25, 0.25], [0.5, 0, 0.5], [0.25, 0.25, 0.5]])
    >>> vfm = var_fmpt(p)
    >>> vfm
    array([[  5.58333333,  12.        ,   6.88888889],
           [  6.22222222,  12.        ,   6.22222222],
           [  6.88888889,  12.        ,   5.58333333]])

    Notes
    -----
    Uses formulation (and examples on p. 83) in Kemeny and Snell (1976) [1]_
    References
    ----------

    .. [1] Kemeny, John, G. and J. Laurie Snell (1976) Finite Markov
       Chains. Springer-Verlag. Berlin
    """
    P = np.asarray(P)
    A = np.linalg.matrix_power(P, 1000)
    n, k = A.shape
    identity_matrix = np.identity(k)
    Z = la.inv(identity_matrix - P + A)
    E = np.ones_like(Z)
    D = np.diag(1.0 / np.diag(A))
    Zdg = np.diag(np.diag(Z))
    M = (identity_matrix - Z + np.multiply(E, Zdg)) * D
    ZM = Z @ M
    ZMdg = np.diag(np.diag(ZM))
    W = M @ (2 * Zdg @ D - identity_matrix) + 2 * (ZM - np.multiply(E, ZMdg))
    return W - np.multiply(M, M)


def _test():
    import doctest

    doctest.testmod(verbose=True)


if __name__ == "__main__":
    _test()
