"""
======================================================
Imputing missing values before building an estimator
======================================================

This example shows that imputing the missing values can give better results
than discarding the samples containing any missing value.
Imputing does not always improve the predictions, so please check via cross-validation.
Sometimes dropping rows or using marker values is more effective.

Missing values can be replaced by the mean, the median, the most frequent
value or the mean of values of k-nearest neighbors using the ``strategy`` hyper-parameter.
The median is a more robust estimator for data with high magnitude variables
which could dominate results (otherwise known as a 'long tail').

Script output::

  Score with the entire dataset = 0.43
  Score without the samples containing missing values = 0.36
  Score after mean imputation of the missing values = 0.42
  Score after knn imputation with 7 neighbors of the missing values = 0.43

In this case, imputing helps the classifier get close to the original score.

"""
import numpy as np

from sklearn.datasets import load_diabetes
from sklearn.ensemble import RandomForestRegressor
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import Imputer
from sklearn.cross_validation import cross_val_score

rng = np.random.RandomState(0)

dataset = load_diabetes()
X_full, y_full = dataset.data, dataset.target
n_samples = X_full.shape[0]
n_features = X_full.shape[1]

#Create a random matrix to randomly make missing values
missing_matrix = np.random.rand(n_samples, n_features)
th = 0.15  # each sample has (1-th)^n_features of probability to have full features
mask = missing_matrix < th
missing_samples = mask.any(1)
full_percentage = (n_samples - missing_samples.sum())/float(n_samples)
print("Percentage of samples with full features: %f" %full_percentage )

# Estimate the score on the entire dataset, with no missing values
estimator = RandomForestRegressor(random_state=0, n_estimators=100)
score = cross_val_score(estimator, X_full, y_full).mean()
print("Score with the entire dataset = %.2f" % score)


# Estimate the score without the lines containing missing values
X_filtered = X_full[~missing_samples, :]
y_filtered = y_full[~missing_samples]
estimator = RandomForestRegressor(random_state=0, n_estimators=100)
score = cross_val_score(estimator, X_filtered, y_filtered).mean()
print("Score without the samples containing missing values = %.2f" % score)

# Estimate the score after mean imputation of the missing values

X_missing = X_full.copy()
X_missing[mask] = np.nan
y_missing = y_full.copy()

estimator = Pipeline([("imputer", Imputer(strategy="mean",
                                          axis=0)),
                      ("forest", RandomForestRegressor(random_state=0,
                                                       n_estimators=100))])
score = cross_val_score(estimator, X_missing, y_missing).mean()
print("Score after mean imputation of the missing values = %.2f" % score)

# Estimate the score after knn imputation of the missing values
neigh = 7
estimator2 = Pipeline([("imputer", Imputer(strategy="knn",
                                           axis=0, n_neighbors=neigh)),
                      ("forest", RandomForestRegressor(random_state=0,
                                                       n_estimators=100))])
score = cross_val_score(estimator2, X_missing, y_missing).mean()
print("Score after knn imputation with %d neighbors of the missing values = %.2f" % (neigh, score))
