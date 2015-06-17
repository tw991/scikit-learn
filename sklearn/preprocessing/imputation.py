# Authors: Nicolas Tresegnie <nicolas.tresegnie@gmail.com>
# License: BSD 3 clause

import warnings
import numpy as np
import numpy.ma as ma
from scipy import sparse
from scipy import stats

from ..base import BaseEstimator, TransformerMixin
from ..utils import check_array
from ..utils import as_float_array
from ..utils.fixes import astype
from ..utils.sparsefuncs import _get_median
from ..utils.validation import check_is_fitted
from ..utils import gen_batches
from ..externals import six

zip = six.moves.zip
map = six.moves.map

__all__ = [
    'Imputer',
]


def _get_mask(X, value_to_mask):
    """Compute the boolean mask X == missing_values."""
    if value_to_mask == "NaN" or np.isnan(value_to_mask):
        return np.isnan(X)
    else:
        return X == value_to_mask


def _most_frequent(array, extra_value, n_repeat):
    """Compute the most frequent value in a 1d array extended with
       [extra_value] * n_repeat, where extra_value is assumed to be not part
       of the array."""
    # Compute the most frequent value in array only
    if array.size > 0:
        mode = stats.mode(array)
        most_frequent_value = mode[0][0]
        most_frequent_count = mode[1][0]
    else:
        most_frequent_value = 0
        most_frequent_count = 0

    # Compare to array + [extra_value] * n_repeat
    if most_frequent_count == 0 and n_repeat == 0:
        return np.nan
    elif most_frequent_count < n_repeat:
        return extra_value
    elif most_frequent_count > n_repeat:
        return most_frequent_value
    elif most_frequent_count == n_repeat:
        # Ties the breaks. Copy the behaviour of scipy.stats.mode
        if most_frequent_value < extra_value:
            return most_frequent_value
        else:
            return extra_value



class Imputer(BaseEstimator, TransformerMixin):
    """Imputation transformer for completing missing values.

    Read more in the :ref:`User Guide <imputation>`.

    Parameters
    ----------
    missing_values : integer or "NaN", optional (default="NaN")
        The placeholder for the missing values. All occurrences of
        `missing_values` will be imputed. For missing values encoded as np.nan,
        use the string value "NaN".

    strategy : string, optional (default="mean")
        The imputation strategy.

        - If "mean", then replace missing values using the mean along
          the axis.
        - If "median", then replace missing values using the median along
          the axis.
        - If "most_frequent", then replace missing using the most frequent
          value along the axis.
        - If "knn", then replace missing using the mean of the k-nearest neighbors
          along the axis.

    axis : integer, optional (default=0)
        The axis along which to impute.

        - If `axis=0`, then impute along columns.
        - If `axis=1`, then impute along rows.

    verbose : integer, optional (default=0)
        Controls the verbosity of the imputer.

    copy : boolean, optional (default=True)
        If True, a copy of X will be created. If False, imputation will
        be done in-place whenever possible. Note that, in the following cases,
        a new copy will always be made, even if `copy=False`:

        - If X is not an array of floating values;
        - If X is sparse and `missing_values=0`;
        - If `axis=0` and X is encoded as a CSR matrix;
        - If `axis=1` and X is encoded as a CSC matrix.

    n_neighbors : int, optional (default=1)
        It only has effect if the `strategy=knn`. It controls the number of nearest
        neighbors used to compute the mean along the axis.

    Attributes
    ----------
    statistics_ : array of shape (n_features,)
        The imputation fill value for each feature if axis == 0.
        If `strategy=knn`, then it contains those samples having no missing value.

    Notes
    -----
    - When ``axis=0``, columns which only contained missing values at `fit`
      are discarded upon `transform`.
    - When ``axis=1``, an exception is raised if there are rows for which it is
      not possible to fill in the missing values (e.g., because they only
      contain missing values).
    - Knn strategy currently doesn't support sparse matrix.
    """
    def __init__(self, missing_values="NaN", strategy="mean",
                 axis=0, verbose=0, copy=True, n_neighbors=1):
        self.missing_values = missing_values
        self.strategy = strategy
        self.axis = axis
        self.verbose = verbose
        self.copy = copy
        self.n_neighbors = n_neighbors

    def fit(self, X, y=None):
        """Fit the imputer on X.

        Parameters
        ----------
        X : {array-like, sparse matrix}, shape (n_samples, n_features)
            Input data, where ``n_samples`` is the number of samples and
            ``n_features`` is the number of features.

        Returns
        -------
        self : object
            Returns self.
        """
        # Check parameters
        allowed_strategies = ["mean", "median", "most_frequent", "knn"]
        if self.strategy not in allowed_strategies:
            raise ValueError("Can only use these strategies: {0} "
                             " got strategy={1}".format(allowed_strategies,
                                                        self.strategy))

        if self.axis not in [0, 1]:
            raise ValueError("Can only impute missing values on axis 0 and 1, "
                             " got axis={0}".format(self.axis))

        # Since two different arrays can be provided in fit(X) and
        # transform(X), the imputation data will be computed in transform()
        # when the imputation is done per sample (i.e., when axis=1).
        if self.axis == 0:
            X = check_array(X, accept_sparse='csc', dtype=np.float64,
                            force_all_finite=False)

            if sparse.issparse(X):
                self.statistics_ = self._sparse_fit(X,
                                                    self.strategy,
                                                    self.missing_values,
                                                    self.axis)
            else:
                self.statistics_ = self._dense_fit(X,
                                                   self.strategy,
                                                   self.missing_values,
                                                   self.axis)

        return self

    def _sparse_fit(self, X, strategy, missing_values, axis):
        """Fit the transformer on sparse data."""
        # Imputation is done "by column", so if we want to do it
        # by row we only need to convert the matrix to csr format.
        if axis == 1:
            X = X.tocsr()
        else:
            X = X.tocsc()

        # Count the zeros
        if missing_values == 0:
            n_zeros_axis = np.zeros(X.shape[not axis], dtype=int)
        else:
            n_zeros_axis = X.shape[axis] - np.diff(X.indptr)

        # Mean
        if strategy == "mean":
            if missing_values != 0:
                n_non_missing = n_zeros_axis

                # Mask the missing elements
                mask_missing_values = _get_mask(X.data, missing_values)
                mask_valids = np.logical_not(mask_missing_values)

                # Sum only the valid elements
                new_data = X.data.copy()
                new_data[mask_missing_values] = 0
                X = sparse.csc_matrix((new_data, X.indices, X.indptr),
                                      copy=False)
                sums = X.sum(axis=0)

                # Count the elements != 0
                mask_non_zeros = sparse.csc_matrix(
                    (mask_valids.astype(np.float64),
                     X.indices,
                     X.indptr), copy=False)
                s = mask_non_zeros.sum(axis=0)
                n_non_missing = np.add(n_non_missing, s)

            else:
                sums = X.sum(axis=axis)
                n_non_missing = np.diff(X.indptr)

            # Ignore the error, columns with a np.nan statistics_
            # are not an error at this point. These columns will
            # be removed in transform
            with np.errstate(all="ignore"):
                return np.ravel(sums) / np.ravel(n_non_missing)

        # Median + Most frequent
        else:
            # Remove the missing values, for each column
            columns_all = np.hsplit(X.data, X.indptr[1:-1])
            mask_missing_values = _get_mask(X.data, missing_values)
            mask_valids = np.hsplit(np.logical_not(mask_missing_values),
                                    X.indptr[1:-1])

            # astype necessary for bug in numpy.hsplit before v1.9
            columns = [col[astype(mask, bool, copy=False)]
                       for col, mask in zip(columns_all, mask_valids)]

            # Median
            if strategy == "median":
                median = np.empty(len(columns))
                for i, column in enumerate(columns):
                    median[i] = _get_median(column, n_zeros_axis[i])

                return median

            # Most frequent
            elif strategy == "most_frequent":
                most_frequent = np.empty(len(columns))

                for i, column in enumerate(columns):
                    most_frequent[i] = _most_frequent(column,
                                                      0,
                                                      n_zeros_axis[i])

                return most_frequent

            elif strategy == "knn":
                raise ValueError("strategy='knn' does not support sparse matrix input")


    def _dense_fit(self, X, strategy, missing_values, axis):
        """Fit the transformer on dense data."""
        X = check_array(X, force_all_finite=False)
        mask = _get_mask(X, missing_values)
        masked_X = ma.masked_array(X, mask=mask)

        # Mean
        if strategy == "mean":
            mean_masked = np.ma.mean(masked_X, axis=axis)
            # Avoid the warning "Warning: converting a masked element to nan."
            mean = np.ma.getdata(mean_masked)
            mean[np.ma.getmask(mean_masked)] = np.nan

            return mean

        # Median
        elif strategy == "median":
            if tuple(int(v) for v in np.__version__.split('.')[:2]) < (1, 5):
                # In old versions of numpy, calling a median on an array
                # containing nans returns nan. This is different is
                # recent versions of numpy, which we want to mimic
                masked_X.mask = np.logical_or(masked_X.mask,
                                              np.isnan(X))
            median_masked = np.ma.median(masked_X, axis=axis)
            # Avoid the warning "Warning: converting a masked element to nan."
            median = np.ma.getdata(median_masked)
            median[np.ma.getmaskarray(median_masked)] = np.nan

            return median

        # Most frequent
        elif strategy == "most_frequent":
            # scipy.stats.mstats.mode cannot be used because it will no work
            # properly if the first element is masked and if it's frequency
            # is equal to the frequency of the most frequent valid element
            # See https://github.com/scipy/scipy/issues/2636

            # To be able access the elements by columns
            if axis == 0:
                X = X.transpose()
                mask = mask.transpose()

            most_frequent = np.empty(X.shape[0])

            for i, (row, row_mask) in enumerate(zip(X[:], mask[:])):
                row_mask = np.logical_not(row_mask).astype(np.bool)
                row = row[row_mask]
                most_frequent[i] = _most_frequent(row, np.nan, 0)

            return most_frequent

        # KNN
        elif strategy == "knn":

            if axis == 1:
                X = X.copy().transpose()

            full_data = X[np.logical_not(mask.any(axis=1))]
            if full_data.size == 0:
                raise ValueError("There is no sample with complete data.")
            if full_data.shape[0] < self.n_neighbors:
                raise ValueError("There are only %d complete samples, but n_neighbors=%d."
                                 % (full_data.shape[0], self.n_neighbors))
            if axis == 1:
                full_data = full_data.transpose()

            return full_data

    def transform(self, X):
        """Impute all missing values in X.

        Parameters
        ----------
        X : {array-like, sparse matrix}, shape = [n_samples, n_features]
            The input data to complete.
        """
        if self.axis == 0:
            check_is_fitted(self, 'statistics_')

        # Copy just once
        X = as_float_array(X, copy=self.copy, force_all_finite=False)

        # Since two different arrays can be provided in fit(X) and
        # transform(X), the imputation data need to be recomputed
        # when the imputation is done per sample
        if self.axis == 1:
            X = check_array(X, accept_sparse='csr', force_all_finite=False,
                            copy=False)

            if sparse.issparse(X):
                statistics = self._sparse_fit(X,
                                              self.strategy,
                                              self.missing_values,
                                              self.axis)

            else:
                statistics = self._dense_fit(X,
                                             self.strategy,
                                             self.missing_values,
                                             self.axis)
        else:
            X = check_array(X, accept_sparse='csc', force_all_finite=False,
                            copy=False)
            statistics = self.statistics_

        # Delete the invalid rows/columns
        invalid_mask = np.isnan(statistics)
        valid_mask = np.logical_not(invalid_mask)
        valid_statistics = statistics[valid_mask]
        valid_statistics_indexes = np.where(valid_mask)[0]

        if self.strategy != "knn":
            missing = np.arange(X.shape[not self.axis])[invalid_mask]

        if self.axis == 0 and invalid_mask.any():
            if self.verbose:
                warnings.warn("Deleting features without "
                              "observed values: %s" % missing)
            X = X[:, valid_statistics_indexes]
        elif self.axis == 1 and invalid_mask.any():
            raise ValueError("Some rows only contain "
                             "missing values: %s" % missing)

        # Do actual imputation
        if sparse.issparse(X) and self.missing_values != 0:
            mask = _get_mask(X.data, self.missing_values)
            indexes = np.repeat(np.arange(len(X.indptr) - 1, dtype=np.int),
                                np.diff(X.indptr))[mask]

            X.data[mask] = astype(valid_statistics[indexes], X.dtype,
                                  copy=False)
        else:
            if sparse.issparse(X):
                X = X.toarray()

            mask = _get_mask(X, self.missing_values)
            n_missing = np.sum(mask, axis=self.axis)

            if self.strategy == 'knn':
                if self.axis == 1:
                    X = X.transpose()
                    mask = mask.transpose()
                    statistics = statistics.transpose()

                batch_size = 10  # set batch size for block query
                if False:
                    missing_index = np.where(mask.any(axis=1))[0]
                    #@jnothman 's method
                    for sl in list(gen_batches(len(missing_index), batch_size)):
                        X_sl = X[missing_index[sl]]
                        test1 = X_sl[:][:, np.newaxis, :] - statistics
                        D2 = test1 ** 2
                        #D2 = (X_sl[missing_index_sl][:, np.newaxis, :] - statistics) ** 2
                        D2[np.isnan(D2)] = 0
                        missing_row, missing_col = np.where(np.isnan(X_sl))
                        sqdist = D2.sum(axis=2)
                        ind = np.argsort(sqdist, axis=1)[:, :self.n_neighbors]
                        means = np.mean(statistics[ind], axis=1)
                        X_sl[missing_row, missing_col] = means[np.where(np.isnan(X_sl))[0],
                                                               missing_col]
                        X[missing_index[sl]] = X_sl
                else:
                    # group by missing features and batch within group
                    group_index = np.unique(mask.astype('u1').view((np.void, X.shape[1])), return_inverse=True)[1]
                    for group_number in range(max(group_index)+1):
                        if group_number == 0:
                            continue
                        else:
                            missing_index = np.where(group_index == group_number)[0]
                            batch_slice = list(gen_batches(len(missing_index), batch_size))
                            for sl in batch_slice:
                                index_sl = missing_index[sl]
                                X_sl = X[index_sl]
                                D2 = (X_sl[:][:, np.newaxis, :] - statistics) ** 2
                                D2[np.isnan(D2)] = 0
                                missing_row, missing_col = np.where(np.isnan(X_sl))
                                sqdist = D2.sum(axis=2)
                                ind = np.argsort(sqdist, axis=1)[:, :self.n_neighbors]
                                means = np.mean(statistics[ind], axis=1)
                                X_sl[missing_row, missing_col] = means[np.where(np.isnan(X_sl))[0],
                                                                       missing_col]
                                X[index_sl] = X_sl



                if self.axis == 1:
                    X = X.transpose()

            else:
                values = np.repeat(valid_statistics, n_missing)

                if self.axis == 0:
                    coordinates = np.where(mask.transpose())[::-1]
                else:
                    coordinates = mask

                X[coordinates] = values

        return X

