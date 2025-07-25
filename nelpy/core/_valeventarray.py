# note:
# veva can be binned, and binning can be accommodative, or accumulative
# accomodative is averaging/interp while accumulative is mass-preserving

# __all__ = ['EventArray',
#            'BinnedEventArray',
#            'SpikeTrainArray',
#            'BinnedSpikeTrainArray']


# __all__ = ['BaseValueEventArray(ABC)',
#            'ValueEventArray(BaseValueEventArray)',
#            'MarkedSpikeTrainArray(ValueEventArray)',
#            'BinnedValueEventArray(ValueEventArray)',
#            'BinnedMarkedSpikeTrainArray(MarkedSpikeTrainArray)']

# __all__ = ['BaseStatefulEventArray(ABC)'
#            'StatefulEventArray(BaseStatefulEventArray)'
#            'BinnedStatefulEventArray(???)']

"""
Notes
-----

non-binned EVAs have multiple timeseries as abscissa
binned EVAs only have a single timeseries as abscissa

ISASA = Irregularly Sampled AnalogSignalArray

eva - not callable
    - fully binnable --> beva

beva - callable --> veva (really ISASA)
     - binnable
     - castable to ASA and back

veva - not callable
     - somewhat binnable --> bveva
     - make stateful --> sveva
     - .data; .values

bveva - callable --> veva (not exactly an ISASA anymore, due to each signal being ND)
      - castable to beva (2d matrix, instead of list of 2d matrices); not complete!

sveva - callable --> veva
      - somewhat binnable --> bveva
      - .data; .values; .states

Remarks: I planned on having a list of arrays, and a list of timeseries for the
         ValueEventArrays.

ValueEventArrays have data structure
    nSeries : nEvents x nValues [(i, j, k) --> i: j=f(i), k]
BinnedValueEventArrays have data structure
    nSeries x nBins x nValues [(i, j, k) --> i x j x k]
and each of these structures are wrapped in intervals[data] pseudo encapsulation

BaseValueEventArray (ABC)
  --- veva (base)
  --- bveva (base)
  --- sveva (veva)

"""

import copy
import logging
from abc import ABC, abstractmethod

import numba
import numpy as np
from numba.typed import List as NumbaList

from .. import core, utils, version
from ..utils_.decorators import keyword_equivalence

__all__ = ["ValueEventArray", "MarkedSpikeTrainArray", "StatefulValueEventArray"]


class IntervalSeriesSlicer(object):
    def __init__(self):
        pass

    def __getitem__(self, *args):
        """intervals (e.g. epochs), series (e.g. units)"""
        # by default, keep all series
        seriesslice = slice(None, None, None)
        if isinstance(*args, int):
            intervalslice = args[0]
        elif isinstance(*args, core.IntervalArray):
            intervalslice = args[0]
        else:
            try:
                slices = np.s_[args]
                slices = slices[0]
                if len(slices) > 2:
                    raise IndexError(
                        "only [intervals, series] slicing is supported at this time!"
                    )
                elif len(slices) == 2:
                    intervalslice, seriesslice = slices
                else:
                    intervalslice = slices[0]
            except TypeError:
                # only interval to slice:
                intervalslice = slices

        return intervalslice, seriesslice


class ItemGetter_loc(object):
    """.loc is primarily label based (that is, series_id based)

    .loc will raise KeyError when the items are not found.

    Allowed inputs are:
        - A single label, e.g. 5 or 'a', (note that 5 is interpreted
            as a label of the index. This use is not an integer
            position along the index)
        - A list or array of labels ['a', 'b', 'c']
        - A slice object with labels 'a':'f', (note that contrary to
            usual python slices, both the start and the stop are
            included!)
    """

    def __init__(self, obj):
        self.obj = obj

    def __getitem__(self, idx):
        """intervals, series"""
        intervalslice, seriesslice = IntervalSeriesSlicer()[idx]

        # first convert series slice into list
        if isinstance(seriesslice, slice):
            start = seriesslice.start
            stop = seriesslice.stop
            istep = seriesslice.step
            try:
                if start is None:
                    istart = 0
                else:
                    istart = self.obj._series_ids.index(start)
            except ValueError:
                raise KeyError(
                    "series_id {} could not be found in BaseEventArray!".format(start)
                )
            try:
                if stop is None:
                    istop = self.obj.n_series
                else:
                    istop = self.obj._series_ids.index(stop) + 1
            except ValueError:
                raise KeyError(
                    "series_id {} could not be found in BaseEventArray!".format(stop)
                )
            if istep is None:
                istep = 1
            if istep < 0:
                istop -= 1
                istart -= 1
                istart, istop = istop, istart
            series_idx_list = list(range(istart, istop, istep))
        else:
            series_idx_list = []
            seriesslice = np.atleast_1d(seriesslice)
            for series in seriesslice:
                try:
                    uidx = self.obj.series_ids.index(series)
                except ValueError:
                    raise KeyError(
                        "series_id {} could not be found in BaseEventArray!".format(
                            series
                        )
                    )
                else:
                    series_idx_list.append(uidx)

        if not isinstance(series_idx_list, list):
            series_idx_list = list(series_idx_list)

        out = copy.copy(self.obj)
        try:
            out._data = out._data[[series_idx_list]]
            singleseries = len(out._data) == 1
        except AttributeError:
            out._data = out._data[[series_idx_list]]
            singleseries = len(out._data) == 1

        if singleseries:
            out._data = np.array([out._data[0]], ndmin=2)
        out._series_ids = list(
            np.atleast_1d(np.atleast_1d(out._series_ids)[[series_idx_list]])
        )
        # TODO: update tags
        if isinstance(intervalslice, slice):
            if (
                intervalslice.start is None
                and intervalslice.stop is None
                and intervalslice.step is None
            ):
                out.loc = ItemGetter_loc(out)
                out.iloc = ItemGetter_iloc(out)
                return out
        out = out._intervalslicer(intervalslice)
        out.loc = ItemGetter_loc(out)
        out.iloc = ItemGetter_iloc(out)
        return out


class ItemGetter_iloc(object):
    """.iloc is primarily integer position based (from 0 to length-1
    of the axis).

    .iloc will raise IndexError if a requested indexer is
    out-of-bounds, except slice indexers which allow out-of-bounds
    indexing. (this conforms with python/numpy slice semantics).

    Allowed inputs are:
        - An integer e.g. 5
        - A list or array of integers [4, 3, 0]
        - A slice object with ints 1:7
    """

    def __init__(self, obj):
        self.obj = obj

    def __getitem__(self, idx):
        """intervals, series"""
        intervalslice, seriesslice = IntervalSeriesSlicer()[idx]
        out = copy.copy(self.obj)
        if isinstance(seriesslice, int):
            seriesslice = [seriesslice]
        out._data = out._data[[seriesslice]]
        singleseries = len(out._data) == 1
        if singleseries:
            out._data = np.array(out._data[[0]], ndmin=2)
        out._series_ids = list(
            np.atleast_1d(np.atleast_1d(out._series_ids)[seriesslice])
        )

        # TODO: update tags
        if isinstance(intervalslice, slice):
            if (
                intervalslice.start is None
                and intervalslice.stop is None
                and intervalslice.step is None
            ):
                out.loc = ItemGetter_loc(out)
                out.iloc = ItemGetter_iloc(out)
                return out
        out = out._intervalslicer(intervalslice)
        out.loc = ItemGetter_loc(out)
        out.iloc = ItemGetter_iloc(out)
        return out


########################################################################
# class BaseValueEventArray
########################################################################
class BaseValueEventArray(ABC):
    """Base class for ValueEventArray and BinnedValueEventArray."""

    __aliases__ = {}
    __attributes__ = ["_fs", "_series_ids"]

    def __init__(
        self,
        *,
        fs=None,
        series_ids=None,
        empty=False,
        abscissa=None,
        ordinate=None,
        **kwargs,
    ):
        self.__version__ = version.__version__
        self.type_name = self.__class__.__name__
        if abscissa is None:
            abscissa = core.Abscissa()  # TODO: integrate into constructor?
        if ordinate is None:
            ordinate = core.Ordinate()  # TODO: integrate into constructor?
        self._abscissa = abscissa
        self._ordinate = ordinate

        series_label = kwargs.pop("series_label", None)
        if series_label is None:
            series_label = "series"
        self._series_label = series_label

        # if an empty object is requested, return it:
        if empty:
            for attr in self.__attributes__:
                exec("self." + attr + " = None")
            self._abscissa.support = type(self._abscissa.support)(empty=True)
            self.loc = ItemGetter_loc(self)
            self.iloc = ItemGetter_iloc(self)
            return

        # set initial fs to None
        self._fs = None
        # then attempt to update the fs; this does input validation:
        self.fs = fs

        # WARNING! we need to ensure that self.n_series can work BEFORE
        # we can set self.series_ids or self.series_labels, since those
        # setters check that the lengths of the inputs are consistent
        # with self.n_series.

        # inherit series IDs if available, otherwise initialize to default
        if series_ids is None:
            series_ids = list(range(1, self.n_series + 1))

        series_ids = np.array(series_ids, ndmin=1)  # standardize series_ids

        self.series_ids = series_ids

        self.loc = ItemGetter_loc(self)
        self.iloc = ItemGetter_iloc(self)

    def __renew__(self):
        """Re-attach slicers and indexers."""
        self.loc = ItemGetter_loc(self)
        self.iloc = ItemGetter_iloc(self)

    def __repr__(self):
        address_str = " at " + str(hex(id(self)))
        return "<BaseValueEventArray" + address_str + ">"

    @abstractmethod
    @keyword_equivalence(this_or_that={"n_intervals": "n_epochs"})
    def partition(self, ds=None, n_intervals=None):
        """Returns a BaseEventArray whose support has been partitioned.

        Parameters
        ----------
        ds : float, optional
            Maximum duration (in seconds), for each interval.
        n_points : int, optional
            Number of intervals. If ds is None and n_intervals is None, then
            default is to use n_intervals = 100

        Returns
        -------
        out : BaseEventArray
            BaseEventArray that has been partitioned.

        Notes
        -----
        Irrespective of whether 'ds' or 'n_intervals' are used, the exact
        underlying support is propagated, and the first and last points
        of the supports are always included, even if this would cause
        n_points or ds to be violated.
        """
        return

    @property
    def isempty(self):
        """(bool) Empty EventArray."""
        try:
            return np.sum([len(st) for st in self.data]) == 0
        except TypeError:
            return True  # this happens when self.data is None

    @property
    def n_series(self):
        """(int) The number of series."""
        try:
            return utils.PrettyInt(len(self.data))
        except TypeError:
            return 0

    @abstractmethod
    def n_values(self):
        """(int) The number of values associated with each event series."""
        return

    @property
    def n_intervals(self):
        if self.isempty:
            return 0
        """(int) The number of underlying intervals."""
        return self._abscissa.support.n_intervals

    @property
    def series_ids(self):
        """Unit IDs contained in the BaseEventArray."""
        return self._series_ids

    def _copy_without_data(self):
        """Return a copy of self, without event datas."""
        out = copy.copy(self)  # shallow copy
        out._data = None
        out = copy.deepcopy(
            out
        )  # just to be on the safe side, but at least now we are not copying the data!
        return out

    def copy(self):
        """Returns a copy of the EventArray."""
        newcopy = copy.deepcopy(self)
        newcopy.__renew__()
        return newcopy

    @property
    def data(self):
        """Event datas in seconds."""
        return self._data

    @property
    def first_event(self):
        """Returns the [time of the] first event across all series."""
        first = np.inf
        for series in self.data:
            if series[0, 0] < first:
                first = series[0, 0]
        return first

    @property
    def last_event(self):
        """Returns the [time of the] last event across all series."""
        last = -np.inf
        for series in self.data:
            if series[-1, 0] > last:
                last = series[-1, 0]
        return last

    @series_ids.setter
    def series_ids(self, val):
        if val is None:
            self._series_ids = None
            return

        if len(val) != self.n_series:
            raise TypeError("series_ids must be of length n_series")

        # Convert to list of integers, handling various input types
        try:
            series_ids = []
            for item in val:
                if hasattr(item, "item") and item.size == 1:
                    # Handle numpy scalars
                    series_ids.append(int(item.item()))
                elif hasattr(item, "__int__") and not hasattr(item, "__len__"):
                    # Handle other numeric types (but not arrays)
                    series_ids.append(int(item))
                elif hasattr(item, "__len__") and len(item) == 1:
                    # Handle single-element arrays/lists
                    series_ids.append(int(item[0]))
                else:
                    raise TypeError(
                        f"Cannot convert {type(item)} with shape {getattr(item, 'shape', 'unknown')} to int"
                    )

            # Check for duplicates
            if len(set(series_ids)) < len(series_ids):
                raise TypeError("duplicate series_ids are not allowed")

            self._series_ids = series_ids
        except (TypeError, ValueError) as e:
            raise TypeError(f"series_ids must be int-like: {e}")

    @property
    def support(self):
        """(nelpy.IntervalArray) The support of the underlying EventArray."""
        return self._abscissa.support

    @support.setter
    def support(self, val):
        """(nelpy.IntervalArray) The support of the underlying EventArray."""
        # modify support
        if isinstance(val, type(self._abscissa.support)):
            self._abscissa.support = val
        elif isinstance(val, (tuple, list)):
            prev_domain = self._abscissa.domain
            self._abscissa.support = type(self._abscissa.support)([val[0], val[1]])
            self._abscissa.domain = prev_domain
        else:
            raise TypeError(
                "support must be of type {}".format(str(type(self._abscissa.support)))
            )
        # restrict data to new support
        self._data = self._restrict_to_interval_array_fast(
            intervalarray=self._abscissa.support, data=self.data, copyover=True
        )

    @property
    def domain(self):
        """(nelpy.IntervalArray) The domain of the underlying EventArray."""
        return self._abscissa.domain

    @domain.setter
    def domain(self, val):
        """(nelpy.IntervalArray) The domain of the underlying EventArray."""
        # modify domain
        if isinstance(val, type(self._abscissa.support)):
            self._abscissa.domain = val
        elif isinstance(val, (tuple, list)):
            self._abscissa.domain = type(self._abscissa.support)([val[0], val[1]])
        else:
            raise TypeError(
                "support must be of type {}".format(str(type(self._abscissa.support)))
            )
        # restrict data to new support
        self._data = self._restrict_to_interval_array_fast(
            intervalarray=self._abscissa.support, data=self.data, copyover=True
        )

    @property
    def fs(self):
        """(float) Sampling rate."""
        return self._fs

    @fs.setter
    def fs(self, val):
        """(float) Sampling rate."""
        if self._fs == val:
            return
        try:
            if val <= 0:
                raise ValueError("sampling rate must be positive")
        except TypeError:
            raise TypeError("sampling rate must be a scalar")
        self._fs = val

    @property
    def label(self):
        """Label pertaining to the source of the event series."""
        if self._label is None:
            logging.warning("label has not yet been specified")
        return self._label

    @label.setter
    def label(self, val):
        if val is not None:
            try:  # cast to str:
                label = str(val)
            except TypeError:
                raise TypeError("cannot convert label to string")
        else:
            label = val
        self._label = label

    def _series_subset(self, series_list):
        """Return a BaseEventArray restricted to a subset of series.

        Parameters
        ----------
        series_list : array-like
            Array or list of series_ids.
        """
        return self.loc[:, series_list]

    def __setattr__(self, name, value):
        # https://stackoverflow.com/questions/4017572/how-can-i-make-an-alias-to-a-non-function-member-attribute-in-a-python-class
        name = self.__aliases__.get(name, name)
        object.__setattr__(self, name, value)

    def __getattr__(self, name):
        # https://stackoverflow.com/questions/4017572/how-can-i-make-an-alias-to-a-non-function-member-attribute-in-a-python-class
        if name == "aliases":
            raise AttributeError  # http://nedbatchelder.com/blog/201010/surprising_getattr_recursion.html
        name = self.__aliases__.get(name, name)
        # return getattr(self, name) #Causes infinite recursion on non-existent attribute
        return object.__getattribute__(self, name)

    @staticmethod
    def _standardize_kwargs(**kwargs):
        """Provide support for easier ValueEventArray keyword arguments.

        kwarg: time <==> timestamps <==> abscissa_vals <==> events
        kwarg: data <==> values <==> marks

        Examples
        --------
        veva = nel.ValueEventArray(time=..., )
        veva = nel.ValueEventArray(timestamps=..., )
        veva = nel.ValueEventArray(abscissa_vals=..., data=... )
        veva = nel.ValueEventArray(events=..., values=... )
        """

        def only_one_of(*args):
            num_non_null_args = 0
            out = None
            for arg in args:
                if arg is not None:
                    num_non_null_args += 1
                    out = arg
            if num_non_null_args > 1:
                raise ValueError("multiple conflicting arguments received")
            return out

        abscissa_vals = kwargs.pop("abscissa_vals", None)
        timestamps = kwargs.pop("timestamps", None)
        time = kwargs.pop("time", None)
        events = kwargs.pop("events", None)
        data = kwargs.pop("data", None)
        values = kwargs.pop("values", None)
        marks = kwargs.pop("marks", None)

        # only one of the above, else raise exception
        events = only_one_of(abscissa_vals, timestamps, time, events)
        values = only_one_of(data, values, marks)

        if events is not None:
            kwargs["events"] = events

        if values is not None:
            kwargs["values"] = values

        return kwargs


########################################################################
# class ValueEventArray
########################################################################
class ValueEventArray(BaseValueEventArray):
    """A multiseries eventarray with shared support.

    Parameters
    ----------
    data : array of np.array(dtype=np.float64) event datas in seconds.
        Array of length n_series, each entry with shape (n_data,)
    fs : float, optional
        Sampling rate in Hz. Default is 30,000
    support : EpochArray, optional
        EpochArray on which eventarrays are defined.
        Default is [0, last event] inclusive.
    label : str or None, optional
        Information pertaining to the source of the eventarray.
    cell_type : list (of length n_series) of str or other, optional
        Identified cell type indicator, e.g., 'pyr', 'int'.
    series_ids : list (of length n_series) of indices corresponding to
        curated data. If no series_ids are specified, then [1,...,n_series]
        will be used. WARNING! The first series will have index 1, not 0!
    meta : dict
        Metadata associated with eventarray.

    Attributes
    ----------
    data : array of np.array(dtype=np.float64) event datas in seconds.
        Array of length n_series, each entry with shape (n_data,)
    support : EpochArray on which eventarray is defined.
    n_events: np.array(dtype=np.int) of shape (n_series,)
        Number of events in each series.
    fs: float
        Sampling frequency (Hz).
    cell_types : np.array of str or other
        Identified cell type for each series.
    label : str or None
        Information pertaining to the source of the eventarray.
    meta : dict
        Metadata associated with eventseries.
    """

    __attributes__ = ["_data"]
    __attributes__.extend(BaseValueEventArray.__attributes__)

    def __init__(
        self,
        events=None,
        values=None,
        *,
        fs=None,
        support=None,
        series_ids=None,
        empty=False,
        **kwargs,
    ):
        self._val_init(
            events=events,
            values=values,
            fs=fs,
            support=support,
            series_ids=series_ids,
            empty=empty,
            **kwargs,
        )

    def _val_init(
        self,
        events=None,
        values=None,
        *,
        fs=None,
        support=None,
        series_ids=None,
        empty=False,
        **kwargs,
    ):
        #############################################
        #            standardize kwargs             #
        #############################################
        if events is not None:
            kwargs["events"] = events
        if values is not None:
            kwargs["values"] = values
        kwargs = self._standardize_kwargs(**kwargs)
        events = kwargs.pop("events", None)
        values = kwargs.pop("values", None)
        #############################################

        # if an empty object is requested, return it:
        if empty:
            super().__init__(empty=True)
            for attr in self.__attributes__:
                exec("self." + attr + " = None")
            self._abscissa.support = type(self._abscissa.support)(empty=True)
            return

        # set default sampling rate
        if fs is None:
            fs = 30000
            logging.info(
                "No sampling rate was specified! Assuming default of {} Hz.".format(fs)
            )

        def is_singletons(data):
            """Returns True if data is a list of singletons (more than one)."""
            # Avoid np.array on jagged input
            if isinstance(data, (list, tuple)) and len(data) > 1:
                # If all elements are scalars or 1-element lists/arrays
                try:
                    if all(
                        (not hasattr(x, "__len__") or len(np.atleast_1d(x)) == 1)
                        for x in data
                    ):
                        return True
                except Exception:
                    return False
            return False

        def is_single_series(data):
            """Returns True if data represents event datas from a single series.

            Examples
            --------
            [1, 2, 3]           : True
            [[1, 2, 3]]         : True
            [[1, 2, 3], []]     : False
            [[], [], []]        : False
            [[[[1, 2, 3]]]]     : True
            [[[[[1],[2],[3]]]]] : False
            """
            # Avoid np.array on jagged input
            try:
                # If first element is a list/array and has more than 1 element, not single series
                if hasattr(data[0], "__len__") and len(data[0]) > 1:
                    # If data[0][0] is also a list/array, too many layers
                    if hasattr(data[0][0], "__len__"):
                        return False
                # If second element exists and is a list/array, not single series
                if len(data) > 1 and hasattr(data[1], "__len__"):
                    return False
            except Exception:
                pass
            return True

        def standardize_to_2d(data):
            # Handle ragged input: list/tuple or np.ndarray of dtype=object
            is_ragged = False
            if isinstance(data, (list, tuple)):
                try:
                    lengths = [len(np.atleast_1d(x)) for x in data]
                    if len(set(lengths)) > 1:
                        is_ragged = True
                except Exception:
                    pass
            elif isinstance(data, np.ndarray) and data.dtype == object:
                try:
                    lengths = [len(np.atleast_1d(x)) for x in data]
                    if len(set(lengths)) > 1:
                        is_ragged = True
                except Exception:
                    pass
            if is_ragged:
                return utils.ragged_array([np.array(st, ndmin=1) for st in data])
            # Only here, if not ragged, use np.array/np.squeeze
            return np.array(np.squeeze(data), ndmin=2)

        def standardize_values_to_2d(data):
            data = standardize_to_2d(data)
            for ii, series in enumerate(data):
                if len(series.shape) == 2:
                    pass
                else:
                    for xx in series:
                        if len(np.atleast_1d(xx)) > 1:
                            raise ValueError(
                                "each series must have a fixed number of values; mismatch in series {}".format(
                                    ii
                                )
                            )
            return data

        events = standardize_to_2d(events)
        values = standardize_values_to_2d(values)

        data = []
        for a, v in zip(events, values):
            data.append(np.vstack((a, v.T)).T)
        # Use ragged_array to support jagged arrays (multi-series with different numbers of events)
        data = utils.ragged_array(data)

        # sort event series, but only if necessary:
        for ii, train in enumerate(events):
            if not utils.is_sorted(train):
                sortidx = np.argsort(train)
                data[ii] = (data[ii])[sortidx, :]

        kwargs["fs"] = fs
        kwargs["series_ids"] = series_ids

        self._data = data  # this is necessary so that
        # super() can determine self.n_series when initializing.

        # initialize super so that self.fs is set:
        super().__init__(**kwargs)

        # print(self.type_name, kwargs)

        # if only empty data were received AND no support, attach an
        # empty support:
        if np.sum([st.size for st in data]) == 0 and support is None:
            logging.warning("no events; cannot automatically determine support")
            support = type(self._abscissa.support)(empty=True)

        # determine eventarray support:
        if support is None:
            self.support = type(self._abscissa.support)(
                np.array([self.first_event, self.last_event + 1 / fs])
            )
        else:
            # restrict events to only those within the eventseries
            # array's support:
            # print('restricting, here')
            self.support = support

        # TODO: if sorted, we may as well use the fast restrict here as well?
        data = self._restrict_to_interval_array_fast(
            intervalarray=self.support, data=data
        )

        self._data = data
        return

    @keyword_equivalence(this_or_that={"n_intervals": "n_epochs"})
    def partition(self, ds=None, n_intervals=None):
        """Returns a BaseEventArray whose support has been partitioned.

        Parameters
        ----------
        ds : float, optional
            Maximum duration (in seconds), for each interval.
        n_points : int, optional
            Number of intervals. If ds is None and n_intervals is None, then
            default is to use n_intervals = 100

        Returns
        -------
        out : BaseEventArray
            BaseEventArray that has been partitioned.

        Notes
        -----
        Irrespective of whether 'ds' or 'n_intervals' are used, the exact
        underlying support is propagated, and the first and last points
        of the supports are always included, even if this would cause
        n_points or ds to be violated.
        """

        out = self.copy()
        abscissa = copy.deepcopy(out._abscissa)
        abscissa.support = abscissa.support.partition(ds=ds, n_intervals=n_intervals)
        out._abscissa = abscissa
        out.__renew__()

        return out

    def __iter__(self):
        """EventArray iterator initialization."""
        self._index = 0
        return self

    def __next__(self):
        """EventArray iterator advancer."""
        index = self._index

        if index > self._abscissa.support.n_intervals - 1:
            raise StopIteration

        self._index += 1
        return self.loc[index]

    def _intervalslicer(self, idx):
        """Helper function to restrict object to EpochArray."""
        # if self.isempty:
        #     return self

        if isinstance(idx, core.IntervalArray):
            if idx.isempty:
                return type(self)(empty=True)
            support = self._abscissa.support.intersect(
                interval=idx, boundaries=True
            )  # what if fs of slicing interval is different?
            if support.isempty:
                return type(self)(empty=True)

            logging.disable(logging.CRITICAL)
            data = self._restrict_to_interval_array_fast(
                intervalarray=support, data=self.data, copyover=True
            )
            eventarray = self._copy_without_data()
            eventarray._data = data
            eventarray._abscissa.support = support
            eventarray.__renew__()
            logging.disable(0)
            return eventarray
        elif isinstance(idx, int):
            eventarray = self._copy_without_data()
            support = self._abscissa.support[idx]
            eventarray._abscissa.support = support
            if (idx >= self._abscissa.support.n_intervals) or idx < (
                -self._abscissa.support.n_intervals
            ):
                eventarray.__renew__()
                return eventarray
            else:
                data = self._restrict_to_interval_array_fast(
                    intervalarray=support, data=self.data, copyover=True
                )
                eventarray._data = data
                eventarray._abscissa.support = support
                eventarray.__renew__()
                return eventarray
        else:  # most likely slice indexing
            try:
                logging.disable(logging.CRITICAL)
                support = self._abscissa.support[idx]
                data = self._restrict_to_interval_array_fast(
                    intervalarray=support, data=self.data, copyover=True
                )
                eventarray = self._copy_without_data()
                eventarray._data = data
                eventarray._abscissa.support = support
                eventarray.__renew__()
                logging.disable(0)
                return eventarray
            except Exception:
                raise TypeError("unsupported subsctipting type {}".format(type(idx)))

    def __getitem__(self, idx):
        """EventArray index access.

        By default, this method is bound to ValueEventArray.loc
        """
        return self.loc[idx]

    @property
    def n_active(self):
        """(int) The number of active series.

        A series is considered active if it fired at least one event.
        """
        if self.isempty:
            return 0
        return utils.PrettyInt(np.count_nonzero(self.n_events))

    @property
    def events(self):
        events = []
        for series in self.data:
            events.append(series[:, 0].squeeze())
        return utils.ragged_array(events)

    @property
    def values(self):
        values = []
        for series in self.data:
            values.append(series[:, 1:].squeeze())
        return utils.ragged_array(values)

    def flatten(self, *, series_id=None):
        """Collapse events across series.

        Parameters
        ----------
        series_id: (int)
            (series) ID to assign to flattened event series, default is 0.
        """
        if self.n_series < 2:  # already flattened
            return self

        # default args:
        if series_id is None:
            series_id = 0

        flattened = self._copy_without_data()

        flattened._series_ids = [series_id]

        raise NotImplementedError
        alldatas = self.data[0]
        for series in range(1, self.n_series):
            alldatas = utils.linear_merge(alldatas, self.data[series])

        flattened._data = np.array(list(alldatas), ndmin=2)
        flattened.__renew__()
        return flattened

    @staticmethod
    def _restrict_to_interval_array_fast(intervalarray, data, copyover=True):
        """Return data restricted to an IntervalArray.

        This function assumes sorted event datas, so that binary search can
        be used to quickly identify slices that should be kept in the
        restriction. It does not check every event data.

        Parameters
        ----------
        intervalarray : IntervalArray or EpochArray
        data : list or array-like, each element of size (n_events, n_values).
        """
        if intervalarray.isempty:
            n_series = len(data)
            data = np.zeros((n_series, 0))
            return data

        singleseries = len(data) == 1  # bool

        # TODO: is this copy even necessary?
        if copyover:
            data = copy.copy(data)

        # NOTE: this used to assume multiple series for the enumeration to work
        for series, evt_data in enumerate(data):
            evt_data = ValueEventArray._to_2d_array(evt_data)
            if evt_data.size == 0 or evt_data.shape[1] < 1:
                if singleseries:
                    data = np.array([[]])
                else:
                    data_ = data.tolist()
                    data_[series] = np.array([])
                    data = utils.ragged_array(data_)
                continue
            indices = []
            for epdata in intervalarray.data:
                t_start = epdata[0]
                t_stop = epdata[1]
                # Ensure we have a proper 1D array of event times
                if evt_data.ndim > 1:
                    event_times = evt_data[:, 0].flatten()
                else:
                    event_times = evt_data.flatten()
                # Ensure event_times is a proper 1D array and not an object array
                if event_times.dtype == object:
                    # Handle object array by extracting the actual values
                    event_times = np.array(
                        [
                            float(t) if hasattr(t, "__float__") else t
                            for t in event_times
                        ]
                    )
                # Ensure event_times is a proper 1D array
                if event_times.size == 0:
                    indices.append((0, 0))
                else:
                    try:
                        frm, to = np.searchsorted(event_times, (t_start, t_stop))
                        indices.append((frm, to))
                    except (ValueError, TypeError):
                        # Fallback: handle case where searchsorted fails
                        indices.append((0, 0))
            indices = np.array(indices, ndmin=2)
            if np.diff(indices).sum() < len(evt_data):
                logging.info("ignoring events outside of eventarray support")
            if singleseries:
                data_list = []
                for start, stop in indices:
                    data_list.extend(evt_data[start:stop])
                data = np.array([data_list])
            else:
                # here we have to do some annoying conversion between
                # arrays and lists to fully support jagged array
                # mutation
                data_list = []
                for start, stop in indices:
                    data_list.extend(evt_data[start:stop])
                data_ = data.tolist()
                data_[series] = np.array(data_list)
                data = utils.ragged_array(data_)
        return data

    def __repr__(self):
        address_str = " at " + str(hex(id(self)))
        logging.disable(logging.CRITICAL)
        if self.isempty:
            return "<empty " + self.type_name + address_str + ">"
        if self._abscissa.support.n_intervals > 1:
            epstr = " ({} segments)".format(self._abscissa.support.n_intervals)
        else:
            epstr = ""
        if self.fs is not None:
            fsstr = " at %s Hz" % self.fs
        else:
            fsstr = ""
        numstr = " %s %s" % (self.n_series, self._series_label)
        logging.disable(0)
        return "<%s%s:%s%s>%s" % (self.type_name, address_str, numstr, epstr, fsstr)

    def bin(self, *, ds=None, method="mean", **kwargs):
        """Return a binned value event array.

        method in [sum, mean, median, min, max] or a custom function.
        Additional keyword arguments are passed to BinnedValueEventArray.
        """
        return BinnedValueEventArray(self, ds=ds, method=method, **kwargs)

    @property
    def n_events(self):
        """(np.array) The number of events in each series."""
        if self.isempty:
            return 0
        return np.array([len(series) for series in self.data])

    @property
    def n_values(self):
        """(int) The number of values associated with each event series."""
        if self.isempty:
            return 0
        n_values = []
        for series in self.data:
            n_values.append(series.squeeze().shape[1] - 1)
        return n_values

    @property
    def issorted(self):
        """(bool) Sorted EventArray."""
        if self.isempty:
            return True
        return np.array(
            [utils.is_sorted(eventarray[:, 0]) for eventarray in self.data]
        ).all()

    def _reorder_series_by_idx(self, neworder, inplace=False):
        """Reorder series according to a specified order.

        neworder must be list-like, of size (n_series,)

        Return
        ------
        out : reordered EventArray
        """

        if inplace:
            out = self
        else:
            out = self.copy()

        oldorder = list(range(len(neworder)))
        for oi, ni in enumerate(neworder):
            frm = oldorder.index(ni)
            to = oi
            utils.swap_rows(out._data, frm, to)
            out._series_ids[frm], out._series_ids[to] = (
                out._series_ids[to],
                out._series_ids[frm],
            )
            # TODO: re-build series tags (tag system not yet implemented)
            oldorder[frm], oldorder[to] = oldorder[to], oldorder[frm]
        out.__renew__()

        return out

    def reorder_series_by_ids(self, neworder, *, inplace=False):
        """Reorder series according to a specified order.

        neworder must be list-like, of size (n_series,) and in terms of
        series_ids

        Return
        ------
        out : reordered EventArray
        """
        if inplace:
            out = self
        else:
            out = self.copy()

        neworder = [self.series_ids.index(x) for x in neworder]

        oldorder = list(range(len(neworder)))
        for oi, ni in enumerate(neworder):
            frm = oldorder.index(ni)
            to = oi
            utils.swap_rows(out._data, frm, to)
            out._series_ids[frm], out._series_ids[to] = (
                out._series_ids[to],
                out._series_ids[frm],
            )
            # TODO: re-build series tags (tag system not yet implemented)
            oldorder[frm], oldorder[to] = oldorder[to], oldorder[frm]

        out.__renew__()
        return out

    def make_stateful(self):
        raise NotImplementedError

    @staticmethod
    def _to_2d_array(arr):
        """Convert array to 2D numpy array, handling object arrays properly."""
        if isinstance(arr, np.ndarray) and arr.dtype == object:
            # Handle object arrays by extracting the actual data
            if arr.size == 1:
                # Single element object array
                return np.atleast_2d(arr[0])
            else:
                # Multiple element object array - concatenate
                flattened = []
                for item in arr:
                    if isinstance(item, np.ndarray):
                        flattened.append(item)
                    else:
                        flattened.append(np.array(item))
                if flattened:
                    return np.vstack(flattened)
                else:
                    return np.array([]).reshape(0, 0)
        else:
            return np.atleast_2d(arr)


# ----------------------------------------------------------------------#
# ======================================================================#


########################################################################
# class MarkedSpikeTrainArray
########################################################################
class MarkedSpikeTrainArray(ValueEventArray):
    """
    MarkedSpikeTrainArray for storing spike times with associated marks (e.g., waveform features).

    This class extends ValueEventArray to support marks for each spike event, such as tetrode features or other metadata.

    Parameters
    ----------
    events : array-like
        Spike times or event times.
    marks : array-like
        Associated marks/features for each event.
    support : nelpy.IntervalArray, optional
        Support intervals for the spike train.
    fs : float, optional
        Sampling frequency in Hz.
    series_label : str, optional
        Label for the series (e.g., 'tetrodes').
    **kwargs :
        Additional keyword arguments passed to the parent class.

    Attributes
    ----------
    events : array-like
        Spike times or event times.
    marks : array-like
        Associated marks/features for each event.
    support : nelpy.IntervalArray
        Support intervals for the spike train.
    fs : float
        Sampling frequency in Hz.
    series_label : str
        Label for the series.

    Examples
    --------
    >>> msta = MarkedSpikeTrainArray(events=spike_times, marks=features, fs=30000)
    >>> msta.events
    array([...])
    >>> msta.marks
    array([...])
    """

    # specify class-specific aliases:
    __aliases__ = {
        "time": "data",
        "_time": "_data",
        "n_epochs": "n_intervals",
        "n_units": "n_series",
        "_unit_subset": "_series_subset",  # requires kw change
        "get_event_firing_order": "get_spike_firing_order",
        "reorder_units_by_ids": "reorder_series_by_ids",
        "reorder_units": "reorder_series",
        "_reorder_units_by_idx": "_reorder_series_by_idx",
        "n_spikes": "n_events",
        "n_marks": "n_values",
        "unit_ids": "series_ids",
        "unit_labels": "series_labels",
        "unit_tags": "series_tags",
        "_unit_ids": "_series_ids",
        "_unit_labels": "_series_labels",
        "_unit_tags": "_series_tags",
        "first_spike": "first_event",
        "last_spike": "last_event",
        "marks": "values",
        "spikes": "events",
    }

    def __init__(self, *args, **kwargs):
        # add class-specific aliases to existing aliases:
        self.__aliases__ = {**super().__aliases__, **self.__aliases__}

        series_label = kwargs.pop("series_label", None)
        if series_label is None:
            series_label = "tetrodes"
        kwargs["series_label"] = series_label

        support = kwargs.get("support", None)
        if support is not None:
            abscissa = kwargs.get("abscissa", core.TemporalAbscissa(support=support))
        else:
            abscissa = kwargs.get("abscissa", core.TemporalAbscissa())
        ordinate = kwargs.get("ordinate", core.AnalogSignalArrayOrdinate())

        kwargs["abscissa"] = abscissa
        kwargs["ordinate"] = ordinate

        super().__init__(*args, **kwargs)

    # @keyword_equivalence(this_or_that={'n_intervals':'n_epochs'})
    # def partition(self, ds=None, n_intervals=None, n_epochs=None):
    #     if n_intervals is None:
    #         n_intervals = n_epochs
    #     kwargs = {'ds':ds, 'n_intervals': n_intervals}
    #     return super().partition(**kwargs)

    def bin(self, *, ds=None):
        """Return a BinnedSpikeTrainArray."""
        raise NotImplementedError
        return BinnedMarkedSpikeTrainArray(self, ds=ds)  # noqa: F821


# ----------------------------------------------------------------------#
# ======================================================================#


########################################################################
# class StatefulValueEventArray
########################################################################
class StatefulValueEventArray(BaseValueEventArray):
    """
    StatefulValueEventArray for storing events with associated values and states.

    Parameters
    ----------
    events : array-like
        Event times for each series. List of arrays, shape (n_series, n_events_i).
    values : array-like
        Values associated with each event. List of arrays, shape (n_series, n_events_i).
    states : array-like
        States associated with each event. List of arrays, shape (n_series, n_events_i).
    support : nelpy.IntervalArray, optional
        Support intervals for the events. If None, inferred from events.
    fs : float, optional
        Sampling frequency in Hz. Default is 30000.
    series_ids : list, optional
        List of series IDs. If None, defaults to [1, ..., n_series].
    empty : bool, optional
        If True, create an empty object.
    **kwargs
        Additional keyword arguments passed to the parent class.

    Attributes
    ----------
    events : np.ndarray
        Event times for each series. Ragged array, shape (n_series, n_events_i).
    values : np.ndarray
        Values for each event. Ragged array, shape (n_series, n_events_i).
    states : np.ndarray
        States for each event. Ragged array, shape (n_series, n_events_i).
    support : nelpy.IntervalArray
        Support intervals for the events.
    fs : float
        Sampling frequency in Hz.
    n_series : int
        Number of series.
    n_events : np.ndarray
        Number of events in each series.
    series_ids : list
        List of series IDs.

    Examples
    --------
    >>> events = [[0.1, 0.5, 1.0], [0.2, 0.6, 1.2]]
    >>> values = [[1, 2, 3], [4, 5, 6]]
    >>> states = [[10, 20, 30], [40, 50, 60]]
    >>> sveva = nel.StatefulValueEventArray(
    ...     events=events, values=values, states=states, fs=10
    ... )
    >>> sveva.n_series
    2
    >>> sveva.n_events
    array([3, 3])
    >>> sveva.events[0]
    array([0.1, 0.5, 1.0])
    >>> sveva.values[0]
    array([1, 2, 3])
    >>> sveva.states[0]
    array([10, 20, 30])
    """

    # specify class-specific aliases:
    __aliases__ = {
        "time": "data",
        "_time": "_data",
        "n_epochs": "n_intervals",
        "n_units": "n_series",
        "_unit_subset": "_series_subset",  # requires kw change
        "get_event_firing_order": "get_spike_firing_order",
        "reorder_units_by_ids": "reorder_series_by_ids",
        "reorder_units": "reorder_series",
        "_reorder_units_by_idx": "_reorder_series_by_idx",
        "n_spikes": "n_events",
        "n_marks": "n_values",
        "unit_ids": "series_ids",
        "unit_labels": "series_labels",
        "unit_tags": "series_tags",
        "_unit_ids": "_series_ids",
        "_unit_labels": "_series_labels",
        "_unit_tags": "_series_tags",
        "first_spike": "first_event",
        "last_spike": "last_event",
        "marks": "values",
        "spikes": "events",
    }

    def __init__(
        self,
        events=None,
        values=None,
        states=None,
        *,
        fs=None,
        support=None,
        series_ids=None,
        empty=False,
        **kwargs,
    ):
        # add class-specific aliases to existing aliases:
        # self.__aliases__ = {**super().__aliases__, **self.__aliases__}
        # print('in init')
        if support is not None:
            abscissa = kwargs.get("abscissa", core.TemporalAbscissa(support=support))
        else:
            abscissa = kwargs.get("abscissa", core.TemporalAbscissa())
        ordinate = kwargs.get("ordinate", core.AnalogSignalArrayOrdinate())

        kwargs["abscissa"] = abscissa
        kwargs["ordinate"] = ordinate

        # print('non-stateful preprocessing')
        self._val_init(
            events=events,
            values=values,
            states=states,
            fs=fs,
            support=support,
            series_ids=series_ids,
            empty=empty,
            **kwargs,
        )

        # print('making stateful')
        data = self._make_stateful(data=self.data)
        self._data = data

    def _val_init(
        self,
        events=None,
        values=None,
        states=None,
        *,
        fs=None,
        support=None,
        series_ids=None,
        empty=False,
        **kwargs,
    ):
        #############################################
        #            standardize kwargs             #
        #############################################
        if events is not None:
            kwargs["events"] = events
        if values is not None:
            kwargs["values"] = values
        if states is not None:
            kwargs["states"] = states
        kwargs = self._standardize_kwargs(**kwargs)
        events = kwargs.pop("events", None)
        values = kwargs.pop("values", None)
        states = kwargs.pop("states", None)
        #############################################

        # if an empty object is requested, return it:
        if empty:
            super().__init__(empty=True)
            for attr in self.__attributes__:
                exec("self." + attr + " = None")
            self._abscissa.support = type(self._abscissa.support)(empty=True)
            return

        # set default sampling rate
        if fs is None:
            fs = 30000
            logging.info(
                "No sampling rate was specified! Assuming default of {} Hz.".format(fs)
            )

        def is_singletons(data):
            """Returns True if data is a list of singletons (more than one)."""
            data = np.array(data)
            try:
                if data.shape[-1] < 2 and np.max(data.shape) > 1:
                    return True
                if max(np.array(data).shape[:-1]) > 1 and data.shape[-1] == 1:
                    return True
            except (IndexError, TypeError, ValueError):
                return False
            return False

        def is_single_series(data):
            """Returns True if data represents event datas from a single series.

            Examples
            ========
            [1, 2, 3]           : True
            [[1, 2, 3]]         : True
            [[1, 2, 3], []]     : False
            [[], [], []]        : False
            [[[[1, 2, 3]]]]     : True
            [[[[[1],[2],[3]]]]] : False
            """
            try:
                if isinstance(data[0][0], list) or isinstance(data[0][0], np.ndarray):
                    logging.info("event datas input has too many layers!")
                    try:
                        if max(np.array(data).shape[:-1]) > 1:
                            #                 singletons = True
                            return False
                    except ValueError:
                        return False
                    data = np.squeeze(data)
            except (IndexError, TypeError):
                pass
            try:
                if isinstance(data[1], list) or isinstance(data[1], np.ndarray):
                    return False
            except (IndexError, TypeError):
                pass
            return True

        def standardize_to_2d(data):
            # Handle ragged input: list/tuple or np.ndarray of dtype=object
            is_ragged = False
            if isinstance(data, (list, tuple)):
                try:
                    lengths = [len(np.atleast_1d(x)) for x in data]
                    if len(set(lengths)) > 1:
                        is_ragged = True
                except Exception:
                    pass
            elif isinstance(data, np.ndarray) and data.dtype == object:
                try:
                    lengths = [len(np.atleast_1d(x)) for x in data]
                    if len(set(lengths)) > 1:
                        is_ragged = True
                except Exception:
                    pass
            if is_ragged:
                return utils.ragged_array([np.array(st, ndmin=1) for st in data])
            # Only here, if not ragged, use np.array/np.squeeze
            return np.array(np.squeeze(data), ndmin=2)

        def standardize_values_to_2d(data):
            data = standardize_to_2d(data)
            for ii, series in enumerate(data):
                if len(series.shape) == 2:
                    pass
                else:
                    for xx in series:
                        if len(np.atleast_1d(xx)) > 1:
                            raise ValueError(
                                "each series must have a fixed number of values; mismatch in series {}".format(
                                    ii
                                )
                            )
            return data

        events = standardize_to_2d(events)
        values = standardize_values_to_2d(values)
        states = standardize_to_2d(states)

        data = []
        for a, v, s in zip(events, values, states):
            data.append(np.vstack((a, v.T, s.T)).T)
        data = np.array(data)

        # sort event series, but only if necessary:
        for ii, train in enumerate(events):
            if not utils.is_sorted(train):
                sortidx = np.argsort(train)
                data[ii] = (data[ii])[sortidx, :]

        kwargs["fs"] = fs
        kwargs["series_ids"] = series_ids

        self._data = data  # this is necessary so that
        # super() can determine self.n_series when initializing.

        # initialize super so that self.fs is set:
        super().__init__(**kwargs)

        # if only empty data were received AND no support, attach an
        # empty support:
        if np.sum([st.size for st in data]) == 0 and support is None:
            logging.warning("no events; cannot automatically determine support")
            support = type(self._abscissa.support)(empty=True)

        # determine eventarray support:
        if support is None:
            self._abscissa.support = type(self._abscissa.support)(
                np.array([self.first_event, self.last_event + 1 / fs])
            )
        else:
            # restrict events to only those within the eventseries
            # array's support:
            self._abscissa.support = support

        # TODO: if sorted, we may as well use the fast restrict here as well?
        data = self._restrict_to_interval_array_fast(
            intervalarray=self.support, data=data
        )

        self._data = data
        return

    @property
    def data(self):
        """Event datas in seconds."""
        return self._data

    @keyword_equivalence(this_or_that={"n_intervals": "n_epochs"})
    def partition(self, ds=None, n_intervals=None):
        """Returns a BaseEventArray whose support has been partitioned.

        Parameters
        ----------
        ds : float, optional
            Maximum duration (in seconds), for each interval.
        n_points : int, optional
            Number of intervals. If ds is None and n_intervals is None, then
            default is to use n_intervals = 100

        Returns
        -------
        out : BaseEventArray
            BaseEventArray that has been partitioned.

        Notes
        -----
        Irrespective of whether 'ds' or 'n_intervals' are used, the exact
        underlying support is propagated, and the first and last points
        of the supports are always included, even if this would cause
        n_points or ds to be violated.
        """

        out = self.copy()
        abscissa = copy.deepcopy(out._abscissa)
        abscissa.support = abscissa.support.partition(ds=ds, n_intervals=n_intervals)
        out._abscissa = abscissa
        out.__renew__()

        return out

    def __iter__(self):
        """EventArray iterator initialization."""
        self._index = 0
        return self

    def __next__(self):
        """EventArray iterator advancer."""
        index = self._index

        if index > self._abscissa.support.n_intervals - 1:
            raise StopIteration

        self._index += 1
        return self.loc[index]

    def __getitem__(self, idx):
        """EventArray index access.

        By default, this method is bound to ValueEventArray.loc
        """
        return self.loc[idx]

    @property
    def support(self):
        """(nelpy.IntervalArray) The support of the underlying EventArray."""
        return self._abscissa.support

    @support.setter
    def support(self, val):
        """(nelpy.IntervalArray) The support of the underlying EventArray."""
        # modify support
        if isinstance(val, type(self._abscissa.support)):
            self._abscissa.support = val
        elif isinstance(val, (tuple, list)):
            prev_domain = self._abscissa.domain
            self._abscissa.support = type(self._abscissa.support)([val[0], val[1]])
            self._abscissa.domain = prev_domain
        else:
            raise TypeError(
                "support must be of type {}".format(str(type(self._abscissa.support)))
            )
        # restrict data to new support
        self._data = self._restrict_to_interval_array_value_fast(
            intervalarray=self._abscissa.support, data=self.data, copyover=True
        )

    @property
    def domain(self):
        """(nelpy.IntervalArray) The domain of the underlying EventArray."""
        return self._abscissa.domain

    @domain.setter
    def domain(self, val):
        """(nelpy.IntervalArray) The domain of the underlying EventArray."""
        # modify domain
        if isinstance(val, type(self._abscissa.support)):
            self._abscissa.domain = val
        elif isinstance(val, (tuple, list)):
            self._abscissa.domain = type(self._abscissa.support)([val[0], val[1]])
        else:
            raise TypeError(
                "support must be of type {}".format(str(type(self._abscissa.support)))
            )
        # restrict data to new support
        self._data = self._restrict_to_interval_array_value_fast(
            intervalarray=self._abscissa.support, data=self.data, copyover=True
        )

    def _intervalslicer(self, idx):
        """Helper function to restrict object to EpochArray."""
        # if self.isempty:
        #     return self

        if isinstance(idx, core.IntervalArray):
            if idx.isempty:
                return type(self)(empty=True)
            support = self._abscissa.support.intersect(
                interval=idx, boundaries=True
            )  # what if fs of slicing interval is different?
            if support.isempty:
                return type(self)(empty=True)

            logging.disable(logging.CRITICAL)
            data = self._restrict_to_interval_array_value_fast(
                intervalarray=support, data=self.data, copyover=True
            )
            eventarray = self._copy_without_data()
            eventarray._data = data
            eventarray._abscissa.support = support
            eventarray.__renew__()
            logging.disable(0)
            return eventarray
        elif isinstance(idx, int):
            eventarray = self._copy_without_data()
            support = self._abscissa.support[idx]
            eventarray._abscissa.support = support
            if (idx >= self._abscissa.support.n_intervals) or idx < (
                -self._abscissa.support.n_intervals
            ):
                eventarray.__renew__()
                return eventarray
            else:
                data = self._restrict_to_interval_array_value_fast(
                    intervalarray=support, data=self.data, copyover=True
                )
                eventarray._data = data
                eventarray._abscissa.support = support
                eventarray.__renew__()
                return eventarray
        else:  # most likely slice indexing
            try:
                logging.disable(logging.CRITICAL)
                support = self._abscissa.support[idx]
                data = self._restrict_to_interval_array_value_fast(
                    intervalarray=support, data=self.data, copyover=True
                )
                eventarray = self._copy_without_data()
                eventarray._data = data
                eventarray._abscissa.support = support
                eventarray.__renew__()
                logging.disable(0)
                return eventarray
            except Exception:
                raise TypeError("unsupported subsctipting type {}".format(type(idx)))

    @staticmethod
    def _restrict_to_interval_array_fast(intervalarray, data, copyover=True):
        """Return data restricted to an IntervalArray.

        This function assumes sorted event datas, so that binary search can
        be used to quickly identify slices that should be kept in the
        restriction. It does not check every event data.

        Parameters
        ----------
        intervalarray : IntervalArray or EpochArray
        data : list or array-like, each element of size (n_events, n_values).
        """
        if intervalarray.isempty:
            n_series = len(data)
            data = np.zeros((n_series, 0))
            return data

        singleseries = len(data) == 1  # bool

        # TODO: is this copy even necessary?
        if copyover:
            data = copy.copy(data)

        # NOTE: this used to assume multiple series for the enumeration to work
        for series, evt_data in enumerate(data):
            indices = []
            for epdata in intervalarray.data:
                t_start = epdata[0]
                t_stop = epdata[1]
                frm, to = np.searchsorted(evt_data[:, 0], (t_start, t_stop))
                indices.append((frm, to))
            indices = np.array(indices, ndmin=2)
            if np.diff(indices).sum() < len(evt_data):
                logging.info("ignoring events outside of eventarray support")
            if singleseries:
                data_list = []
                for start, stop in indices:
                    data_list.extend(evt_data[start:stop])
                data = np.array([data_list])
            else:
                # here we have to do some annoying conversion between
                # arrays and lists to fully support jagged array
                # mutation
                data_list = []
                for start, stop in indices:
                    data_list.extend(evt_data[start:stop])
                data_ = data.tolist()
                data_[series] = np.array(data_list)
                data = utils.ragged_array(data_)
        return data

    def _restrict_to_interval_array_value_fast(
        self, intervalarray, data, copyover=True
    ):
        """Return data restricted to an IntervalArray.

        This function assumes sorted event datas, so that binary search can
        be used to quickly identify slices that should be kept in the
        restriction. It does not check every event data.

        Parameters
        ----------
        intervalarray : IntervalArray or EpochArray
        data : list or array-like, each element of size (n_events, n_values).
        """
        if intervalarray.isempty:
            n_series = len(data)
            data = np.zeros((n_series, 0))
            return data

        # plan of action
        # create pseudo events supporting each interval
        # then restrict existing data (pseudo and real events)
        # then merge in all pseudo events that don't exist yet
        starts = intervalarray.starts
        stops = intervalarray.stops

        kinds = []
        events = []
        states = []

        for series in data:
            tvect = series[:, 0].astype(float)
            statevals = series[:, 2:]

            kind = []
            state = []

            for start in starts:
                idx = np.max((np.searchsorted(tvect, start, side="right") - 1, 0))
                kind.append(0)
                state.append(statevals[[idx]])

            for stop in stops:
                idx = np.max((np.searchsorted(tvect, stop, side="right") - 1, 0))
                kind.append(2)
                state.append(statevals[[idx]])

            states.append(np.array(state).squeeze())  ## squeeze???
            events.append(np.hstack((starts, stops)))
            kinds.append(np.array(kind))

        pseudodata = []
        for e, k, s in zip(events, kinds, states):
            pseudodata.append(np.vstack((e, k, s.T)).T)

        pseudodata = utils.ragged_array(pseudodata)

        singleseries = len(data) == 1  # bool

        # TODO: is this copy even necessary?
        if copyover:
            data = copy.copy(data)

        # NOTE: this used to assume multiple series for the enumeration to work
        for series, evt_data in enumerate(data):
            indices = []
            for epdata in intervalarray.data:
                t_start = epdata[0]
                t_stop = epdata[1]
                frm, to = np.searchsorted(evt_data[:, 0], (t_start, t_stop))
                indices.append((frm, to))
            indices = np.array(indices, ndmin=2)
            if np.diff(indices).sum() < len(evt_data):
                logging.info("ignoring events outside of eventarray support")
            if singleseries:
                data_list = []
                for start, stop in indices:
                    data_list.extend(evt_data[start:stop])
                data = np.array([data_list])
            else:
                # here we have to do some annoying conversion between
                # arrays and lists to fully support jagged array
                # mutation
                data_list = []
                for start, stop in indices:
                    data_list.extend(evt_data[start:stop])
                data_ = data.tolist()
                data_[series] = np.array(data_list)
                data = utils.ragged_array(data_)

        # now add in all pseudo events that don't already exist in data

        kinds = []
        events = []
        states = []

        for pseries, series in zip(pseudodata, data):
            ptvect = pseries[:, 0].astype(float)
            pkind = pseries[:, 1].astype(int)
            pstatevals = pseries[:, 2:]

            try:
                tvect = series[:, 0].astype(float)
                kind = series[:, 1]
                statevals = series[:, 2:]
            except IndexError:
                tvect = np.zeros((0))
                kind = np.zeros((0))
                statevals = np.zeros((0,))

            for tt, kk, psv in zip(ptvect, pkind, pstatevals):
                # print(tt, kk, psv)
                idx = np.searchsorted(tvect, tt, side="right")
                idx2 = np.max((idx - 1, 0))
                try:
                    if tt == tvect[idx2]:
                        pass
                        # print('pseudo event {} not necessary...'.format(tt))
                    else:
                        # print('pseudo event {} necessary...'.format(tt))
                        kind = np.insert(kind, idx, kk)
                        tvect = np.insert(tvect, idx, tt)
                        statevals = np.insert(statevals, idx, psv, axis=0)
                except IndexError:
                    kind = np.insert(kind, idx, kk)
                    tvect = np.insert(tvect, idx, tt)
                    statevals = np.insert(statevals, idx, psv, axis=0)

            states.append(np.array(statevals).squeeze())
            events.append(tvect)
            kinds.append(kind)

        # print(states)
        # print(tvect)
        # print(kinds)

        data = []
        for e, k, s in zip(events, kinds, states):
            data.append(np.vstack((e, k, s.T)).T)

        data = utils.ragged_array(data)

        return data

    def bin(self, *, ds=None):
        """Return a BinnedValueEventArray."""
        raise NotImplementedError
        return BinnedValueEventArray(self, ds=ds)

    def __call__(self, *args):
        """StatefulValueEventArray callable method; by default returns state values"""
        values = []
        for events, vals in zip(self.state_events, self.state_values):
            idx = np.searchsorted(events, args, side="right") - 1
            idx[idx < 0] = 0
            values.append(vals[[idx]])
        values = np.asarray(values)
        return values

    def _make_stateful(self, data, intervalarray=None, initial_state=np.nan):
        """
        [i, e0, e1, e2, ..., f] for every epoch

        matrix of size (n_values x (n_epochs*2 + n_events) )
        matrix of size (nSeries: n_values x (n_epochs*2 + n_events) )

        needs to change when calling loc, iloc, restrict, getitem, ...

        TODO: initial_state is not used yet!!!
        """
        kinds = []
        events = []
        states = []

        if intervalarray is None:
            intervalarray = self.support

        for series in data:
            starts = intervalarray.starts
            stops = intervalarray.stops
            tvect = series[:, 0].astype(float)
            statevals = series[:, 1:]
            kind = np.ones(tvect.size).astype(int)

            for start in starts:
                idx = np.searchsorted(tvect, start, side="right")
                idx2 = np.max((idx - 1, 0))
                if start == tvect[idx2]:
                    continue
                else:
                    kind = np.insert(kind, idx, 0)
                    tvect = np.insert(tvect, idx, start)
                    statevals = np.insert(statevals, idx, statevals[idx2], axis=0)

            for stop in stops:
                idx = np.searchsorted(tvect, stop, side="right")
                idx2 = np.max((idx - 1, 0))
                if stop == tvect[idx2]:
                    continue
                else:
                    kind = np.insert(kind, idx, 2)
                    tvect = np.insert(tvect, idx, stop)
                    statevals = np.insert(statevals, idx, statevals[idx2], axis=0)

            states.append(statevals)
            events.append(tvect)
            kinds.append(kind)

        data = []
        for e, k, s in zip(events, kinds, states):
            data.append(np.vstack((e, k, s.T)).T)
        data = utils.ragged_array(data)

        return data

    @property
    def n_values(self):
        """(int) The number of values associated with each event series."""
        if self.isempty:
            return 0
        n_values = []
        for series in self.data:
            n_values.append(series.squeeze().shape[1] - 2)
        return n_values

    def __repr__(self):
        address_str = " at " + str(hex(id(self)))
        logging.disable(logging.CRITICAL)
        if self.isempty:
            return "<empty " + self.type_name + address_str + ">"
        if self._abscissa.support.n_intervals > 1:
            epstr = " ({} segments)".format(self._abscissa.support.n_intervals)
        else:
            epstr = ""
        if self.fs is not None:
            fsstr = " at %s Hz" % self.fs
        else:
            fsstr = ""
        numstr = " %s %s" % (self.n_series, self._series_label)
        logging.disable(0)
        return "<%s%s:%s%s>%s" % (self.type_name, address_str, numstr, epstr, fsstr)

    @property
    def n_events(self):
        """(np.array) The number of events in each series."""
        if self.isempty:
            return 0
        return np.array([len(series) for series in self.events])

    @property
    def events(self):
        events = []
        for series, kinds in zip(self.state_events, self.state_kinds):
            keep_idx = np.argwhere(kinds == 1)
            events.append(series[keep_idx].squeeze())
        return np.asarray(events)

    @property
    def values(self):
        values = []
        for series, kinds in zip(self.state_values, self.state_kinds):
            keep_idx = np.argwhere(kinds == 1)
            values.append(series[keep_idx].squeeze())
        return np.asarray(values)

    @property
    def state_events(self):
        events = []
        for series in self.data:
            events.append(series[:, 0].squeeze())

        return np.asarray(events)

    @property
    def state_values(self):
        values = []
        for series in self.data:
            values.append(series[:, 2:].squeeze())

        return np.asarray(values)

    @property
    def state_kinds(self):
        values = []
        for series in self.data:
            values.append(series[:, 1].squeeze())

        return np.asarray(values)

    def _plot(self, *args, **kwargs):
        if self.n_series > 1:
            raise NotImplementedError
        if np.any(np.array(self.n_values) > 1):
            raise NotImplementedError

        import matplotlib.pyplot as plt

        events = self.state_events.squeeze()
        values = self.state_values.squeeze()
        kinds = self.state_kinds.squeeze()

        for (a, b), val, (ka, kb) in zip(
            utils.pairwise(events), values, utils.pairwise(kinds)
        ):
            if kb == 1:
                plt.plot(
                    [a, b],
                    [val, val],
                    "-",
                    color="b",
                    markerfacecolor="w",
                    lw=1.5,
                    mew=1.5,
                )
            if ka == 1:
                plt.plot(
                    [a, b],
                    [val, val],
                    "-",
                    color="g",
                    markerfacecolor="w",
                    lw=1.5,
                    mew=1.5,
                )
            if kb == 1:
                plt.plot(b, val, "o", color="k", markerfacecolor="w", lw=1.5, mew=1.5)
            if ka == 1:
                plt.plot(a, val, "o", color="k", markerfacecolor="k", lw=1.5, mew=1.5)
            if ka == 0 and kb == 2:
                plt.plot(
                    [a, b],
                    [val, val],
                    "-",
                    color="r",
                    markerfacecolor="w",
                    lw=1.5,
                    mew=1.5,
                )


# ----------------------------------------------------------------------#
# ======================================================================#


class BinnedValueEventArray(BaseValueEventArray):
    """A binned representation of ValueEventArray data.

    This class creates a time-binned version of a ValueEventArray by aggregating
    event values within specified time bins. For each support interval, bins are
    defined globally (not per-series) using np.linspace as in BinnedEventArray.
    All series use the same bins for a given interval.

    Parameters
    ----------
    vea : ValueEventArray
        The source ValueEventArray to be binned.
    ds : float
        Bin size in seconds. Bins are created with left-inclusive, right-exclusive
        boundaries (e.g., [t, t+ds)).
    method : str or callable, default="mean"
        Aggregation method for combining values within each bin:
        - "sum": Sum all values in the bin
        - "mean": Average of all values in the bin
        - "median": Median of all values in the bin
        - "min": Minimum value in the bin
        - "max": Maximum value in the bin
        - callable: Custom aggregation function that takes an array and returns a scalar

    Attributes
    ----------
    vea : ValueEventArray
        The original ValueEventArray used to create this binned version.
    ds : float
        The bin size in seconds.
    method : str or callable
        The aggregation method used for binning.
    data : np.ndarray
        Binned data array of shape (n_series, n_bins, n_values) where:
        - n_series: Number of event series
        - n_bins: Total number of bins across all intervals
        - n_values: Number of values per event
    bins : np.ndarray
        Array of bin edges used for binning.
    bin_centers : np.ndarray
        Array of bin center times.
    n_series : int
        Number of event series.
    n_bins : int
        Total number of bins.
    n_values : int
        Number of values per event.
    fs : float or None
        Sampling frequency (inherited from source ValueEventArray).
    support : IntervalArray
        Support intervals (inherited from source ValueEventArray).

    Notes
    -----
    - Binning is performed per-series, with each series having its own bin
      boundaries starting from its minimum event time within each interval.
    - Bins use left-inclusive, right-exclusive boundaries [t, t+ds).
    - Events outside the support intervals are ignored.
    - Empty bins are filled with NaN values.
    - The resulting data maintains the same number of series and values as
      the original ValueEventArray.

    Examples
    --------
    >>> import nelpy as nel
    >>> events = [[0.1, 0.5, 1.0], [0.2, 0.6, 1.2]]
    >>> values = [[1, 2, 3], [4, 5, 6]]
    >>> vea = nel.ValueEventArray(events=events, values=values, fs=10)
    >>> bvea = nel.BinnedValueEventArray(vea, ds=0.5, method="sum")
    >>> print(bvea.data.shape)  # (2, n_bins, 1)
    >>> print(bvea.bin_centers)  # Array of bin center times
    """

    def __init__(self, vea, ds, method="mean"):
        # Set _data to a placeholder with the correct number of series and values
        n_series = vea.n_series
        n_values = (
            max(vea.n_values)
            if hasattr(vea, "n_values") and len(vea.n_values) > 0
            else 1
        )
        self._data = np.empty((n_series, 0, n_values))
        # Call base class constructor with metadata from vea, but don't copy series_ids
        super().__init__(
            fs=getattr(vea, "fs", None),
            series_ids=None,  # Let the base class handle default series_ids
            abscissa=getattr(vea, "_abscissa", None),
            ordinate=getattr(vea, "_ordinate", None),
            empty=False,
        )
        self.vea = vea
        self.ds = ds
        self.method = method
        self._bin()

    def _bin(self):
        """Perform binning operation on the ValueEventArray data, matching BinnedEventArray logic.

        For each support interval, bins are defined globally (not per-series) using np.linspace as in BinnedEventArray.
        All series use the same bins for a given interval. np.histogram is used for each value column for each series.
        Aggregation is performed using the specified method (sum, mean, etc.).
        """
        support = getattr(self.vea, "support", None)
        if support is None:
            # Fallback: use all events if no support is defined
            all_events = np.concatenate([np.asarray(ev) for ev in self.vea.events])
            if all_events.size == 0:
                self._data = np.zeros((self.vea.n_series, 0, self.vea.n_values[0]))
                self._bins = np.array([])
                self._bin_centers = np.array([])
                return
            tmin = np.min(all_events)
            tmax = np.max(all_events)
            intervals = [(tmin, tmax)]
        else:
            intervals = list(zip(support.starts, support.stops))

        n_series = self.vea.n_series
        n_values = max(self.vea.n_values)
        all_binned = []
        all_bins = []
        all_bin_centers = []

        if isinstance(self.method, str):
            method_str = self.method
            if method_str in ("sum", "mean", "min", "max", "median"):
                use_numba = True
            else:
                use_numba = False
        elif callable(self.method):
            method_str = None
            use_numba = False
        else:
            raise ValueError("method must be a string or callable")

        for start, stop in intervals:
            interval_length = stop - start
            if interval_length < self.ds:
                continue
            n_bins = int(np.floor(interval_length / self.ds))
            bins = np.linspace(start, start + n_bins * self.ds, n_bins + 1)
            bin_centers = bins[:-1] + (self.ds / 2)
            binned = np.full((n_series, n_bins, n_values), np.nan)

            if use_numba and n_values == 1:
                # Convert to numba.typed.List to avoid reflected list warning
                try:
                    events_list = NumbaList()
                    for ev in self.vea.events:
                        events_list.append(np.asarray(ev))
                    values_list = NumbaList()
                    for val in self.vea.values:
                        values_list.append(np.asarray(val))
                except Exception:
                    # Fallback to regular list if numba.typed.List is not available
                    events_list = [np.asarray(ev) for ev in self.vea.events]
                    values_list = [np.asarray(val) for val in self.vea.values]
                binned_agg = _bin_ragged_agg_numba(
                    events_list, values_list, bins, method_str
                )
                binned[:, :, 0] = binned_agg
            else:
                for i, (ev, val) in enumerate(zip(self.vea.events, self.vea.values)):
                    ev = np.asarray(ev)
                    val = np.asarray(val)
                    mask_interval = (ev >= start) & (ev < stop)
                    ev_in = ev[mask_interval]
                    val_in = val[mask_interval]
                    if ev_in.size == 0:
                        continue
                    for v in range(n_values):
                        if val_in.ndim == 1:
                            vals = val_in
                        else:
                            vals = val_in[:, v]
                        if method_str == "sum":
                            hist, _ = np.histogram(ev_in, bins=bins, weights=vals)
                            binned[i, :, v] = hist
                        else:
                            inds = np.digitize(ev_in, bins, right=False) - 1
                            for b in range(n_bins):
                                vals_in_bin = vals[inds == b]
                                if vals_in_bin.size > 0:
                                    if method_str == "mean":
                                        binned[i, b, v] = np.mean(vals_in_bin)
                                    elif method_str == "min":
                                        binned[i, b, v] = np.min(vals_in_bin)
                                    elif method_str == "max":
                                        binned[i, b, v] = np.max(vals_in_bin)
                                    elif method_str == "median":
                                        binned[i, b, v] = np.median(vals_in_bin)
                                    else:
                                        binned[i, b, v] = self.method(vals_in_bin)
            all_binned.append(binned)
            all_bins.append(bins)
            all_bin_centers.append(bin_centers)

        if all_binned:
            self._data = np.concatenate(all_binned, axis=1)
            self._bins = np.concatenate(
                [b[:-1] for b in all_bins] + [all_bins[-1][-1:]]
            )
            self._bin_centers = np.concatenate(all_bin_centers)
        else:
            self._data = np.zeros((n_series, 0, n_values))
            self._bins = np.array([])
            self._bin_centers = np.array([])

    @property
    def data(self):
        return self._data

    @property
    def bins(self):
        return self._bins

    @property
    def bin_centers(self):
        return self._bin_centers

    def __repr__(self):
        return f"<BinnedValueEventArray: {self.n_series} series, {self.n_bins} bins, {self.n_values} values, ds={self.ds}>"

    def __getitem__(self, idx):
        # Slicing by IntervalArray/EpochArray
        if hasattr(idx, "starts") and hasattr(idx, "stops"):
            mask = np.zeros_like(self.bin_centers, dtype=bool)
            for start, stop in zip(idx.starts, idx.stops):
                mask |= (self.bin_centers >= start) & (self.bin_centers < stop)
            new_obj = copy.copy(self)
            new_obj._data = self.data[:, mask, :]
            # Adjust bins and bin_centers
            # bins: keep edges that bracket the selected bin_centers
            bin_indices = np.where(mask)[0]
            if len(bin_indices) == 0:
                new_obj._bins = np.array([])
                new_obj._bin_centers = np.array([])
            else:
                new_obj._bins = self.bins[bin_indices[0] : bin_indices[-1] + 2]
                new_obj._bin_centers = self.bin_centers[mask]
            return new_obj
        else:
            raise NotImplementedError(
                "Only slicing by IntervalArray/EpochArray is supported."
            )

    @property
    def n_series(self):
        return self.data.shape[0]

    @property
    def n_bins(self):
        return self.data.shape[1]

    @property
    def n_values(self):
        return self.data.shape[2]

    @property
    def fs(self):
        return getattr(self, "_fs", None)

    @fs.setter
    def fs(self, value):
        self._fs = value

    @property
    def support(self):
        return getattr(self.vea, "support", None)

    def partition(self, ds=None, n_intervals=None):
        """Returns a BinnedValueEventArray whose support has been partitioned.

        Parameters
        ----------
        ds : float, optional
            Maximum duration (in seconds), for each interval.
        n_intervals : int, optional
            Number of intervals. If ds is None and n_intervals is None, then
            default is to use n_intervals = 100

        Returns
        -------
        out : BinnedValueEventArray
            BinnedValueEventArray that has been partitioned.
        """
        out = self.copy()
        abscissa = copy.deepcopy(out._abscissa)
        abscissa.support = abscissa.support.partition(ds=ds, n_intervals=n_intervals)
        out._abscissa = abscissa
        out.__renew__()
        return out

    @staticmethod
    def _to_2d_array(arr):
        """Convert array to 2D numpy array, handling object arrays properly."""
        if isinstance(arr, np.ndarray) and arr.dtype == object:
            # Handle object arrays by extracting the actual data
            if arr.size == 1:
                # Single element object array
                return np.atleast_2d(arr[0])
            else:
                # Multiple element object array - concatenate
                flattened = []
                for item in arr:
                    if isinstance(item, np.ndarray):
                        flattened.append(item)
                    else:
                        flattened.append(np.array(item))
                if flattened:
                    return np.vstack(flattened)
                else:
                    return np.array([]).reshape(0, 0)
        else:
            return np.atleast_2d(arr)

    def smooth(
        self, *, sigma=None, inplace=False, truncate=None, within_intervals=False
    ):
        """Smooth BinnedValueEventArray by convolving with a Gaussian kernel.

        Smoothing is applied in data, and the same smoothing is applied
        to each series in a BinnedValueEventArray.

        Smoothing is applied within each interval by default.

        Parameters
        ----------
        sigma : float, optional
            Standard deviation of Gaussian kernel, in seconds. Default is 0.01 (10 ms)
        truncate : float, optional
            Bandwidth outside of which the filter value will be zero. Default is 4.0
        inplace : bool, optional
            If True the data will be replaced with the smoothed data. Default is False.
        within_intervals : bool, optional
            If True, smooth within each interval. If False, smooth across intervals. Default is False.

        Returns
        -------
        out : BinnedValueEventArray
            New BinnedValueEventArray with smoothed data.
        """
        from .. import utils  # local import to avoid circular import

        if truncate is None:
            truncate = 4
        if sigma is None:
            sigma = 0.01  # 10 ms default
        fs = 1 / self.ds if self.ds else None
        return utils.gaussian_filter(
            self,
            fs=fs,
            sigma=sigma,
            truncate=truncate,
            inplace=inplace,
            within_intervals=within_intervals,
        )

    @property
    def _abscissa_vals(self):
        """(np.array) The bin centers (in seconds)."""
        return self._bin_centers

    @property
    def lengths(self):
        """Lengths of contiguous segments, in number of bins."""
        if (
            self.n_bins == 0
            or self.support is None
            or getattr(self.support, "isempty", False)
        ):
            return 0
        # Each interval: count how many bin_centers fall within it
        lengths = []
        for start, stop in zip(self.support.starts, self.support.stops):
            mask = (self.bin_centers >= start) & (self.bin_centers < stop)
            lengths.append(np.sum(mask))
        return np.array(lengths)


@numba.njit(cache=True)
def _bin_ragged_agg_numba(
    events_list, values_list, bins, method, max_events_per_bin=128
):
    n_series = len(events_list)
    n_bins = len(bins) - 1
    out = np.full((n_series, n_bins), np.nan)
    if method == "sum" or method == "mean":
        counts = np.zeros((n_series, n_bins), dtype=np.int64)
        for i in range(n_series):
            ev = events_list[i]
            val = values_list[i]
            for j in range(len(ev)):
                idx = np.searchsorted(bins, ev[j], side="right") - 1
                if 0 <= idx < n_bins:
                    if np.isnan(val[j]):
                        continue
                    if np.isnan(out[i, idx]):
                        out[i, idx] = 0.0
                    out[i, idx] += val[j]
                    counts[i, idx] += 1
        if method == "mean":
            for i in range(n_series):
                for b in range(n_bins):
                    if counts[i, b] > 0:
                        out[i, b] /= counts[i, b]
                    else:
                        out[i, b] = np.nan
        elif method == "sum":
            for i in range(n_series):
                for b in range(n_bins):
                    if np.isnan(out[i, b]):
                        out[i, b] = 0.0
        return out
    elif method == "min" or method == "max" or method == "median":
        # Use a fixed-size buffer for each bin
        buffer = np.full((n_series, n_bins, max_events_per_bin), np.nan)
        counts = np.zeros((n_series, n_bins), dtype=np.int64)
        for i in range(n_series):
            ev = events_list[i]
            val = values_list[i]
            for j in range(len(ev)):
                idx = np.searchsorted(bins, ev[j], side="right") - 1
                if 0 <= idx < n_bins:
                    c = counts[i, idx]
                    if c < max_events_per_bin:
                        buffer[i, idx, c] = val[j]
                        counts[i, idx] += 1
        for i in range(n_series):
            for b in range(n_bins):
                n = counts[i, b]
                if n == 0:
                    out[i, b] = np.nan
                else:
                    vals = buffer[i, b, :n]
                    if method == "min":
                        out[i, b] = np.nanmin(vals)
                    elif method == "max":
                        out[i, b] = np.nanmax(vals)
                    elif method == "median":
                        out[i, b] = np.nanmedian(vals)
        return out
    else:
        # Not supported
        return out
