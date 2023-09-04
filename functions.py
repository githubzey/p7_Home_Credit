#!/usr/bin/env python
# coding: utf-8

# In[ ]:
import sys
import time
import pandas as pd
import numpy as np
import seaborn as sns
import matplotlib
import matplotlib.pyplot as plt

import missingno as msno
import sklearn
import warnings
import gc
from contextlib import contextmanager
from lightgbm import LGBMClassifier
from sklearn.metrics import roc_auc_score, roc_curve
from sklearn.model_selection import KFold, StratifiedKFold
warnings.simplefilter(action='ignore', category=FutureWarning)

@contextmanager
def timer(title):
    t0 = time.time()
    yield
    print("{} - done in {:.0f}s".format(title, time.time() - t0))

# One-hot encoding for categorical columns with get_dummies
def one_hot_encoder(df, nan_as_category = True):
    original_columns = list(df.columns)
    categorical_columns = [col for col in df.columns if df[col].dtype == 'object']
    df = pd.get_dummies(df, columns= categorical_columns, dummy_na= nan_as_category)
    boolean_cols = [col for col in df.columns if df[col].dtype == bool]
    df[boolean_cols] = df[boolean_cols].astype(int)
    new_columns = [c for c in df.columns if c not in original_columns]
    return df, new_columns

# Preprocess application_train.csv and application_test.csv
def application_train_test(num_rows = None, nan_as_category = False):
    # Read data and merge
    df = pd.read_csv('input/application_train.csv', nrows= num_rows)
    test_df = pd.read_csv('input/application_test.csv', nrows= num_rows)
    print("Train samples: {}, test samples: {}".format(len(df), len(test_df)))
    df = pd.concat([df, test_df], ignore_index=True).reset_index(drop=True)
    # Optional: Remove 4 applications with XNA CODE_GENDER (train set)
    df = df[df['CODE_GENDER'] != 'XNA']
    
    # Categorical features with Binary encode (0 or 1; two categories)
    for bin_feature in ['CODE_GENDER', 'FLAG_OWN_CAR', 'FLAG_OWN_REALTY']:
        df[bin_feature], uniques = pd.factorize(df[bin_feature])
    # Categorical features with One-Hot encode
    df, cat_cols = one_hot_encoder(df, nan_as_category)
    
    # NaN values for DAYS_EMPLOYED: 365.243 -> nan
    df['DAYS_EMPLOYED'].replace(365243, np.nan, inplace= True)
    # Some simple new features (percentages)
    df['DAYS_EMPLOYED_PERC'] = df['DAYS_EMPLOYED'] / df['DAYS_BIRTH']
    df['INCOME_CREDIT_PERC'] = df['AMT_INCOME_TOTAL'] / df['AMT_CREDIT']
    df['INCOME_PER_PERSON'] = df['AMT_INCOME_TOTAL'] / df['CNT_FAM_MEMBERS']
    df['ANNUITY_INCOME_PERC'] = df['AMT_ANNUITY'] / df['AMT_INCOME_TOTAL']
    df['PAYMENT_RATE'] = df['AMT_ANNUITY'] / df['AMT_CREDIT']
    del test_df
    gc.collect()
    return df

# Preprocess bureau.csv and bureau_balance.csv
def bureau_and_balance(num_rows = None, nan_as_category = True):
    bureau = pd.read_csv('input/bureau.csv', nrows = num_rows)
    bb = pd.read_csv('input/bureau_balance.csv', nrows = num_rows)
    bb, bb_cat = one_hot_encoder(bb, nan_as_category)
    bureau, bureau_cat = one_hot_encoder(bureau, nan_as_category)
    
    # Bureau balance: Perform aggregations and merge with bureau.csv
    bb_aggregations = {'MONTHS_BALANCE': ['min', 'max', 'size']}
    for col in bb_cat:
        bb_aggregations[col] = ['mean']
    bb_agg = bb.groupby('SK_ID_BUREAU').agg(bb_aggregations)
    bb_agg.columns = pd.Index([e[0] + "_" + e[1].upper() for e in bb_agg.columns.tolist()])
    bureau = bureau.join(bb_agg, how='left', on='SK_ID_BUREAU')
    bureau.drop(['SK_ID_BUREAU'], axis=1, inplace= True)
    del bb, bb_agg
    gc.collect()
    
    # Bureau and bureau_balance numeric features
    num_aggregations = {
        'DAYS_CREDIT': ['min', 'max', 'mean', 'var'],
        'DAYS_CREDIT_ENDDATE': ['min', 'max', 'mean'],
        'DAYS_CREDIT_UPDATE': ['mean'],
        'CREDIT_DAY_OVERDUE': ['max', 'mean'],
        'AMT_CREDIT_MAX_OVERDUE': ['mean'],
        'AMT_CREDIT_SUM': ['max', 'mean', 'sum'],
        'AMT_CREDIT_SUM_DEBT': ['max', 'mean', 'sum'],
        'AMT_CREDIT_SUM_OVERDUE': ['mean'],
        'AMT_CREDIT_SUM_LIMIT': ['mean', 'sum'],
        'AMT_ANNUITY': ['max', 'mean'],
        'CNT_CREDIT_PROLONG': ['sum'],
        'MONTHS_BALANCE_MIN': ['min'],
        'MONTHS_BALANCE_MAX': ['max'],
        'MONTHS_BALANCE_SIZE': ['mean', 'sum']
    }
    # Bureau and bureau_balance categorical features
    cat_aggregations = {}
    for cat in bureau_cat: cat_aggregations[cat] = ['mean']
    for cat in bb_cat: cat_aggregations[cat + "_MEAN"] = ['mean']
    
    bureau_agg = bureau.groupby('SK_ID_CURR').agg({**num_aggregations, **cat_aggregations})
    bureau_agg.columns = pd.Index(['BURO_' + e[0] + "_" + e[1].upper() for e in bureau_agg.columns.tolist()])
    # Bureau: Active credits - using only numerical aggregations
    active = bureau[bureau['CREDIT_ACTIVE_Active'] == 1]
    active_agg = active.groupby('SK_ID_CURR').agg(num_aggregations)
    active_agg.columns = pd.Index(['ACTIVE_' + e[0] + "_" + e[1].upper() for e in active_agg.columns.tolist()])
    bureau_agg = bureau_agg.join(active_agg, how='left', on='SK_ID_CURR')
    del active, active_agg
    gc.collect()
    # Bureau: Closed credits - using only numerical aggregations
    closed = bureau[bureau['CREDIT_ACTIVE_Closed'] == 1]
    closed_agg = closed.groupby('SK_ID_CURR').agg(num_aggregations)
    closed_agg.columns = pd.Index(['CLOSED_' + e[0] + "_" + e[1].upper() for e in closed_agg.columns.tolist()])
    bureau_agg = bureau_agg.join(closed_agg, how='left', on='SK_ID_CURR')
    del closed, closed_agg, bureau
    gc.collect()
    return bureau_agg

# Preprocess previous_applications.csv
def previous_applications(num_rows = None, nan_as_category = True):
    prev = pd.read_csv('input/previous_application.csv', nrows = num_rows)
    prev, cat_cols = one_hot_encoder(prev, nan_as_category= True)
    # Days 365.243 values -> nan
    prev['DAYS_FIRST_DRAWING'].replace(365243, np.nan, inplace= True)
    prev['DAYS_FIRST_DUE'].replace(365243, np.nan, inplace= True)
    prev['DAYS_LAST_DUE_1ST_VERSION'].replace(365243, np.nan, inplace= True)
    prev['DAYS_LAST_DUE'].replace(365243, np.nan, inplace= True)
    prev['DAYS_TERMINATION'].replace(365243, np.nan, inplace= True)
    # Add feature: value ask / value received percentage
    prev['APP_CREDIT_PERC'] = prev['AMT_APPLICATION'] / prev['AMT_CREDIT']
    # Previous applications numeric features
    num_aggregations = {
        'AMT_ANNUITY': ['min', 'max', 'mean'],
        'AMT_APPLICATION': ['min', 'max', 'mean'],
        'AMT_CREDIT': ['min', 'max', 'mean'],
        'APP_CREDIT_PERC': ['min', 'max', 'mean', 'var'],
        'AMT_DOWN_PAYMENT': ['min', 'max', 'mean'],
        'AMT_GOODS_PRICE': ['min', 'max', 'mean'],
        'HOUR_APPR_PROCESS_START': ['min', 'max', 'mean'],
        'RATE_DOWN_PAYMENT': ['min', 'max', 'mean'],
        'DAYS_DECISION': ['min', 'max', 'mean'],
        'CNT_PAYMENT': ['mean', 'sum'],
  }
    # Previous applications categorical features
    cat_aggregations = {}
    for cat in cat_cols:
        cat_aggregations[cat] = ['mean']
    
    prev_agg = prev.groupby('SK_ID_CURR').agg({**num_aggregations, **cat_aggregations})
    prev_agg.columns = pd.Index(['PREV_' + e[0] + "_" + e[1].upper() for e in prev_agg.columns.tolist()])
    # Previous Applications: Approved Applications - only numerical features
    approved = prev[prev['NAME_CONTRACT_STATUS_Approved'] == 1]
    approved_agg = approved.groupby('SK_ID_CURR').agg(num_aggregations)
    approved_agg.columns = pd.Index(['APPROVED_' + e[0] + "_" + e[1].upper() for e in approved_agg.columns.tolist()])
    prev_agg = prev_agg.join(approved_agg, how='left', on='SK_ID_CURR')
    # Previous Applications: Refused Applications - only numerical features
    refused = prev[prev['NAME_CONTRACT_STATUS_Refused'] == 1]
    refused_agg = refused.groupby('SK_ID_CURR').agg(num_aggregations)
    refused_agg.columns = pd.Index(['REFUSED_' + e[0] + "_" + e[1].upper() for e in refused_agg.columns.tolist()])
    prev_agg = prev_agg.join(refused_agg, how='left', on='SK_ID_CURR')
    del refused, refused_agg, approved, approved_agg, prev
    gc.collect()
    return prev_agg


# Preprocess POS_CASH_balance.csv
def pos_cash(num_rows = None, nan_as_category = True):
    pos = pd.read_csv('input/POS_CASH_balance.csv', nrows = num_rows)
    pos, cat_cols = one_hot_encoder(pos, nan_as_category= True)
    # Features
    aggregations = {
        'MONTHS_BALANCE': ['max', 'mean', 'size'],
        'SK_DPD': ['max', 'mean'],
        'SK_DPD_DEF': ['max', 'mean']
    }
    for cat in cat_cols:
        aggregations[cat] = ['mean']
    
    pos_agg = pos.groupby('SK_ID_CURR').agg(aggregations)
    pos_agg.columns = pd.Index(['POS_' + e[0] + "_" + e[1].upper() for e in pos_agg.columns.tolist()])
    # Count pos cash accounts
    pos_agg['POS_COUNT'] = pos.groupby('SK_ID_CURR').size()
    del pos
    gc.collect()
    return pos_agg

# Preprocess installments_payments.csv
def installments_payments(num_rows = None, nan_as_category = True):
    ins = pd.read_csv('input/installments_payments.csv', nrows = num_rows)
    ins, cat_cols = one_hot_encoder(ins, nan_as_category= True)
    # Percentage and difference paid in each installment (amount paid and installment value)
    ins['PAYMENT_PERC'] = ins['AMT_PAYMENT'] / ins['AMT_INSTALMENT']
    ins['PAYMENT_DIFF'] = ins['AMT_INSTALMENT'] - ins['AMT_PAYMENT']
    # Days past due and days before due (no negative values)
    ins['DPD'] = ins['DAYS_ENTRY_PAYMENT'] - ins['DAYS_INSTALMENT']
    ins['DBD'] = ins['DAYS_INSTALMENT'] - ins['DAYS_ENTRY_PAYMENT']
    ins['DPD'] = ins['DPD'].apply(lambda x: x if x > 0 else 0)
    ins['DBD'] = ins['DBD'].apply(lambda x: x if x > 0 else 0)
    # Features: Perform aggregations
    aggregations = {
        'NUM_INSTALMENT_VERSION': ['nunique'],
        'DPD': ['max', 'mean', 'sum'],
        'DBD': ['max', 'mean', 'sum'],
        'PAYMENT_PERC': ['max', 'mean', 'sum', 'var'],
        'PAYMENT_DIFF': ['max', 'mean', 'sum', 'var'],
        'AMT_INSTALMENT': ['max', 'mean', 'sum'],
        'AMT_PAYMENT': ['min', 'max', 'mean', 'sum'],
        'DAYS_ENTRY_PAYMENT': ['max', 'mean', 'sum']
    }
    for cat in cat_cols:
        aggregations[cat] = ['mean']
    ins_agg = ins.groupby('SK_ID_CURR').agg(aggregations)
    ins_agg.columns = pd.Index(['INSTAL_' + e[0] + "_" + e[1].upper() for e in ins_agg.columns.tolist()])
    # Count installments accounts
    ins_agg['INSTAL_COUNT'] = ins.groupby('SK_ID_CURR').size()
    del ins
    gc.collect()
    return ins_agg


# Preprocess credit_card_balance.csv
def credit_card_balance(num_rows = None, nan_as_category = True):
    cc = pd.read_csv('input/credit_card_balance.csv', nrows = num_rows)
    cc, cat_cols = one_hot_encoder(cc, nan_as_category= True)
    # General aggregations
    cc.drop(['SK_ID_PREV'], axis= 1, inplace = True)
    cc_agg = cc.groupby('SK_ID_CURR').agg(['min', 'max', 'mean', 'sum', 'var'])
    cc_agg.columns = pd.Index(['CC_' + e[0] + "_" + e[1].upper() for e in cc_agg.columns.tolist()])
    # Count credit card lines
    cc_agg['CC_COUNT'] = cc.groupby('SK_ID_CURR').size()
    del cc
    gc.collect()
    return cc_agg

# LightGBM GBDT with KFold or Stratified KFold
# Parameters from Tilii kernel: https://www.kaggle.com/tilii7/olivier-lightgbm-parameters-by-bayesian-opt/code
def kfold_lightgbm(df, num_folds, stratified = False, debug= False):
    # Divide in training/validation and test data
    train_df = df[df['TARGET'].notnull()]
    test_df = df[df['TARGET'].isnull()]
    print("Starting LightGBM. Train shape: {}, test shape: {}".format(train_df.shape, test_df.shape))
    del df
    gc.collect()
    # Cross validation model
    if stratified:
        folds = StratifiedKFold(n_splits= num_folds, shuffle=True, random_state=1001)
    else:
        folds = KFold(n_splits= num_folds, shuffle=True, random_state=1001)
    # Create arrays and dataframes to store results
    oof_preds = np.zeros(train_df.shape[0])
    sub_preds = np.zeros(test_df.shape[0])
    feature_importance_df = pd.DataFrame()
    feats = [f for f in train_df.columns if f not in ['TARGET','SK_ID_CURR','SK_ID_BUREAU','SK_ID_PREV','index']]
    
    for n_fold, (train_idx, valid_idx) in enumerate(folds.split(train_df[feats], train_df['TARGET'])):
        train_x, train_y = train_df[feats].iloc[train_idx], train_df['TARGET'].iloc[train_idx]
        valid_x, valid_y = train_df[feats].iloc[valid_idx], train_df['TARGET'].iloc[valid_idx]

        # LightGBM parameters found by Bayesian optimization
        clf = LGBMClassifier(
            nthread=4,
            n_estimators=10000,
            learning_rate=0.02,
            num_leaves=34,
            colsample_bytree=0.9497036,
            subsample=0.8715623,
            max_depth=8,
            reg_alpha=0.041545473,
            reg_lambda=0.0735294,
            min_split_gain=0.0222415,
            min_child_weight=39.3259775,
            silent=-1,
            verbose=-1, )

        clf.fit(train_x, train_y, eval_set=[(train_x, train_y), (valid_x, valid_y)], 
            eval_metric= 'auc', verbose= 200, early_stopping_rounds= 200)

        oof_preds[valid_idx] = clf.predict_proba(valid_x, num_iteration=clf.best_iteration_)[:, 1]
        sub_preds += clf.predict_proba(test_df[feats], num_iteration=clf.best_iteration_)[:, 1] / folds.n_splits

        fold_importance_df = pd.DataFrame()
        fold_importance_df["feature"] = feats
        fold_importance_df["importance"] = clf.feature_importances_
        fold_importance_df["fold"] = n_fold + 1
        feature_importance_df = pd.concat([feature_importance_df, fold_importance_df], axis=0)
        print('Fold %2d AUC : %.6f' % (n_fold + 1, roc_auc_score(valid_y, oof_preds[valid_idx])))
        del clf, train_x, train_y, valid_x, valid_y
        gc.collect()

    print('Full AUC score %.6f' % roc_auc_score(train_df['TARGET'], oof_preds))
    # Write submission file and plot feature importance
    if not debug:
        test_df['TARGET'] = sub_preds
        test_df[['SK_ID_CURR', 'TARGET']].to_csv(submission_file_name, index= False)
    display_importances(feature_importance_df)
    return feature_importance_df

# Display/plot feature importance
def display_importances(feature_importance_df_):
    cols = feature_importance_df_[["feature", "importance"]].groupby("feature").mean().sort_values(by="importance", ascending=False)[:40].index
    best_features = feature_importance_df_.loc[feature_importance_df_.feature.isin(cols)]
    plt.figure(figsize=(8, 10))
    sns.barplot(x="importance", y="feature", data=best_features.sort_values(by="importance", ascending=False))
    plt.title('LightGBM Features (avg over folds)')
    plt.tight_layout()
    plt.savefig('lgbm_importances01.png')



def main(debug = False):
    num_rows = 10000 if debug else None
    df = application_train_test(num_rows)
    
# Now you can proceed with training your LightGBM model

    with timer("Process bureau and bureau_balance"):
        bureau = bureau_and_balance(num_rows)
        print("Bureau df shape:", bureau.shape)
        df = df.join(bureau, how='left', on='SK_ID_CURR')
        del bureau
        gc.collect()
    with timer("Process previous_applications"):
        prev = previous_applications(num_rows)
        print("Previous applications df shape:", prev.shape)
        df = df.join(prev, how='left', on='SK_ID_CURR')
        del prev
        gc.collect()
    with timer("Process POS-CASH balance"):
        pos = pos_cash(num_rows)
        print("Pos-cash balance df shape:", pos.shape)
        df = df.join(pos, how='left', on='SK_ID_CURR')
        del pos
        gc.collect()
    with timer("Process installments payments"):
        ins = installments_payments(num_rows)
        print("Installments payments df shape:", ins.shape)
        df = df.join(ins, how='left', on='SK_ID_CURR')
        del ins
        gc.collect()
    with timer("Process credit card balance"):
        cc = credit_card_balance(num_rows)
        print("Credit card balance df shape:", cc.shape)
        df = df.join(cc, how='left', on='SK_ID_CURR')
        import re # me
        df = df.rename(columns = lambda x:re.sub('[^A-Za-z0-9_]+', '', x)) # me
        del cc
        gc.collect()
    #with timer("Run LightGBM with kfold"):
        #feat_importance = kfold_lightgbm(df, num_folds= 10, stratified= False, debug= debug)
        
        return df


def pourcentage_null_text(df):
    """
    On donne l'infos pourcentage des valeurs nulles pour le dataframe en texte
    :param df: dataframe
    :return: none
    """
    all_num_data = df.shape[0] * df.shape[1]
    num_null = df.isnull().sum().sum()
    pourcentage_null = ((num_null / all_num_data) * 100).round(2)
    print("** On a ", pourcentage_null, "% de valeur nulle dans notre dataframe **")
    return pourcentage_null

def per_null_dataframe(df):
    """
    On donne l'infos pourcentage des valeurs nulles pour le dataframe en détaille
    On display pourcentage des valeurs nulles par colonne en descendant
    :param df: dataframe
    :return: le dataframe des pourcentages
    """
    per_null = (df.isnull().mean() * 100).round(2)
    nombre_null = df.isnull().sum()
    null_df = pd.concat([nombre_null, per_null], axis=1)
    null_df.columns = ["nombre_null", "pourcentage_null"]
    return null_df.sort_values("nombre_null", ascending=False)

def remove_columns_with_high_nulls(df, threshold=30):
    """
    Remove columns from DataFrame that have more than a specified percentage of null values.
    
    :param df: DataFrame
    :param threshold: Percentage threshold for null values (default is 30)
    :return: DataFrame with specified columns removed
    """
    per_null = (df.isnull().mean() * 100).round(2)
    columns_to_remove = per_null[per_null > threshold].index
    df_cleaned = df.drop(columns=columns_to_remove)
    return df_cleaned


def graph_null(data):
    # On fait un graphique pour illustrer les pourcentages
    all_num_data = data.shape[0] * data.shape[1]
    num_null = data.isnull().sum().sum()
    pourcentage_null = ((num_null / all_num_data) * 100).round(2)
    plt.figure(figsize = (3,4))
    plt.rcParams['font.size'] = 10.0
    pie_null = [num_null, all_num_data - num_null ]
    plt.pie(pie_null, labels=['Null', 'Not_null'], autopct="%0.2f%%", pctdistance=0.6, labeldistance=1.2)
    plt.title("Le pourcentage des valeurs nulles dans le dataframe");
    #plt.legend();

def info_general(df):
    """
    On donne les infos générales pour le dataframe
    :param df: dataframe
    :return: none
    """
    print("*" * 25, "* INFORMATIONS GENERALES DE NOTRE DATAFRAME *", "*" * 25)
    print("-" * 100)
    shape_df = df.shape
    print("" * 100)
    print(
        "Il y a",
        shape_df[0],
        "lignes et",
        shape_df[1],
        "colonnes dans notre dataframe.",
    )
    print("-" * 100, sep="\n")
    print("* On obtiens l'info sur notre dataframe *", "" * 100)
    print("-" * 100, sep="\n")
    print(df.info())
    print("-" * 100, sep="\n")
    print("* On obtient les informations statistiques sur notre dataframe *", "" * 100)
    print("-" * 100, sep="\n")
    display(df.describe(include="all").T)
    print("-" * 100, sep="\n")
    print("*On vérifie s'il y a des doublons dans notre dataframe*", "" * 100)
    search_dup = df.duplicated().sum()
    if search_dup == 0:
        print("** Il n'y a pas de doublons **")
    else:
        print("** Il y a ", search_dup, " doublons **")
    print("-" * 100)
    print(
        "** On vérifie s'il y a des valeurs nulles et "
        "on display pourcentage des valeurs nulles par colonne en descendant **"
    )
    print("-" * 100)
    pourcentage_null = pourcentage_null_text(df)
    #if pourcentage_null > 0:
        #display(per_null_dataframe(df))
        #print("** Visualisation des valeurs nulles **", "" * 100)
        #msno.bar(df)
    #else:
       # pourcentage_null
    #print("-" * 100, sep="\n")

    return 

def display_nulls(df, col):
    """
    On display des valeurs nulles pour le dataframe
    :param df: dataframe
    :param col: la colonne qu'on cherche les valeurs nulles
    :return: le dataframe avec des valeurs nulles
    """
    display_null = pd.isnull(df[col])
    df_null = df[display_null]
    return df_null

def delete_nulls(df, col):
    """
    On supprime des valeurs nulles pour le dataframe
    :param df: dataframe
    :param col: la colonne qu'on va supprimer les valeurs nulles
    :return: le dataframe avec des valeurs sans nulles
    """
    display_null = pd.isnull(df[col])
    df_sans_null = df[~display_null]
    return df_sans_null

def detect_outliers(df, col, ecart_accepte):
    """
    On trouve les valeurs abérrantes dans la dataframe
    :param df: dataframe
    :col: la colonne de dataframe
    :return: les limits des outliers et les valeurs des outliers
    """
    q1 = df[col].quantile(q=0.25)
    q3 = df[col].quantile(q=0.75)
    diff = q3 - q1
    lower_bound = q1 - ecart_accepte * diff
    upper_bound = q3 + ecart_accepte * diff
    outliers = df[(df[col] > upper_bound) | (df[col] < lower_bound)]
    print("lower_bound =", lower_bound, "upper_bound =", upper_bound)
    return outliers

def delete_all_nulls(df):
    """
    On supprime des colonnes complètement nulles pour le dataframe
    :param df: dataframe
    :return: le dataframe avec des valeurs sans nulles
    """
    null_df = (df.isnull().mean() * 100).round(2).reset_index()
    null_df.columns = ["colonne", "pourcentage_null"]
    null_df_full = null_df[null_df["pourcentage_null"] == 100]
    col_not_null = null_df[null_df["pourcentage_null"] < 100]["colonne"]
    df_col_notnull = df[col_not_null]
    print("On a supprimé", null_df_full.shape[0], " colonnes.")
    return df_col_notnull

def heatmap(data):
    sns.set(font_scale=1.2)
    fig = plt.figure(figsize=(20, 8))
    matrix = data.corr()
    plt.title("Le Heatmap de la Data", size=25)
    mask = np.triu(np.ones_like(matrix, dtype=bool))
    sns.heatmap(matrix, annot=True, cmap="coolwarm", mask=mask)
    plt.xticks(rotation=90)
    return

def impute_missing_data(df):
    X = pd.concat(
        [pd.get_dummies(df.select_dtypes("O")), df.select_dtypes(exclude="O")], axis=1
    )

    imputer = KNNImputer(n_neighbors=4)
    imputation_res = pd.DataFrame(imputer.fit_transform(X))
    imputation_res.columns = X.columns
    imputation_res = imputation_res[
        [
            col
            for col in imputation_res.columns
            if not col.startswith("PrimaryPropertyType")
        ]
    ]
    imputation_res = imputation_res.reset_index(drop=True)
    imputation_res.insert(0, "PrimaryPropertyType", df["PrimaryPropertyType"])
    return imputation_res


def drop_duplicates(df, column):
    # Find duplicate rows based on the specified column
    duplicate_rows = df.duplicated(subset=column)

    if duplicate_rows.any():
        # Count the number of duplicate rows
        num_duplicates = duplicate_rows.sum()
        
        # Drop the duplicate rows based on the specified column
        df.drop_duplicates(subset=column, inplace=True)
        print(f"{num_duplicates} duplicate rows have been dropped.")
    else:
        print("No duplicate rows found.")
    return df


def countplot_top10(df, col, figsize=(9, 4)):
    unique_values = df[col].nunique()

    df_ranked = df[col].astype(str).value_counts().reset_index()
    df_ranked.columns = [col, "Count"]
    df_ranked = df_ranked.sort_values("Count", ascending=False).head(10)

    total_count = df_ranked["Count"].sum()
    df_ranked["Percentage"] = (df_ranked["Count"] / total_count) * 100
    print("Il y a ", unique_values, "unique ", col, "dans notre data.")
    sns.set_style("white")  # Remove gridlines
    plt.figure(figsize=(figsize[0], figsize[1]))  # Adjust the figure size as needed

    ax = sns.barplot(x="Count", y=col, data=df_ranked, orient="h")
    plt.xlabel("Count", size=11)
    plt.ylabel(col, size=13)
    plt.title("Top 10 Répartition du " + col, size=13)

    for i in range(df_ranked.shape[0]):
        count = df_ranked["Count"].iloc[i]
        percentage = df_ranked["Percentage"].iloc[i]
        ax.text(count, i, f"{percentage:.2f}%", va="center", fontdict=dict(fontsize=10))
    ax.set_xticklabels(ax.get_xticklabels(), fontsize=10)
    ax.set_yticklabels(ax.get_yticklabels(), fontsize=10)
    # Adjust the x-axis limits if needed
    plt.xlim(0, df_ranked["Count"].max() * 1.1)

    plt.tight_layout()
    plt.show()


def countplot(df, col, figsize=(9, 4)):
    df_ranked = df[col].astype(str).value_counts().reset_index()
    df_ranked.columns = [col, "Count"]
    df_ranked = df_ranked.sort_values("Count", ascending=False)

    total_count = df_ranked["Count"].sum()
    df_ranked["Percentage"] = (df_ranked["Count"] / total_count) * 100

    sns.set_style("white")  # Remove gridlines
    plt.figure(figsize=(figsize[0], figsize[1]))  # Adjust the figure size as needed

    ax = sns.barplot(x="Count", y=col, data=df_ranked, orient="h")
    plt.xlabel("Count", size=11)
    plt.ylabel(col, size=13)
    plt.title("Répartition du " + col, size=13)

    for i in range(df_ranked.shape[0]):
        count = df_ranked["Count"].iloc[i]
        percentage = df_ranked["Percentage"].iloc[i]
        ax.text(count, i, f"{percentage:.2f}%", va="center", fontdict=dict(fontsize=10))
    ax.set_xticklabels(ax.get_xticklabels(), fontsize=10)
    ax.set_yticklabels(ax.get_yticklabels(), fontsize=10)
    # Adjust the x-axis limits if needed
    plt.xlim(0, df_ranked["Count"].max() * 1.1)

    plt.tight_layout()
    plt.show()

def distplot_and_boxplot(df, columns, figsize=(12, 3)):
    if not isinstance(columns, list):
        columns = [columns]

    num_columns = len(columns)
    fig, axes = plt.subplots(
        num_columns, 2, figsize=(figsize[0], figsize[1] * num_columns)
    )

    for i, col in enumerate(columns):
        if num_columns == 1:  # Adjust subplots if only one column is provided
            ax_distplot = axes[0]
            ax_boxplot = axes[1]
        else:
            ax_distplot = axes[i, 0]
            ax_boxplot = axes[i, 1]

        # Distplot
        sns.histplot(df[col], ax=ax_distplot, kde=True)
        ax_distplot.set_title("Distribution Plot - " + col)
        ax_distplot.set_xlabel(col)

        # Boxplot
        sns.boxplot(x=df[col], ax=ax_boxplot)
        ax_boxplot.set_title("Box Plot - " + col)
        ax_boxplot.set_xlabel(col)

    plt.tight_layout()
    plt.show()

def kdeplot_and_boxplot(df, columns, figsize=(12, 3)):
    if not isinstance(columns, list):
        columns = [columns]

    num_columns = len(columns)
    fig, axes = plt.subplots(
        num_columns, 2, figsize=(figsize[0], figsize[1] * num_columns)
    )

    for i, col in enumerate(columns):
        if num_columns == 1:  # Adjust subplots if only one column is provided
            ax_kdeplot = axes[0]
            ax_boxplot = axes[1]
        else:
            ax_kdeplot = axes[i, 0]
            ax_boxplot = axes[i, 1]

        # KDE Plot
        sns.kdeplot(data=df, x=col, ax=ax_kdeplot)
        ax_kdeplot.set_title("Kde Plot - " + col)
        ax_kdeplot.set_xlabel(col)

        # Boxplot
        sns.boxplot(data=df, x=col, ax=ax_boxplot)
        ax_boxplot.set_title("Box Plot - " + col)
        ax_boxplot.set_xlabel(col)

    plt.tight_layout()
    plt.show()

def kdeplot_and_boxplot_by_target(df, target_col, feature_cols, figsize=(12, 5)):
    num_feature_cols = len(feature_cols)
    fig, axes = plt.subplots(
        num_feature_cols, 2, figsize=(figsize[0], figsize[1] * num_feature_cols)
    )

    for i, col in enumerate(feature_cols):
        if num_feature_cols == 1:  # Adjust subplots if only one feature column is provided
            ax_kdeplot = axes[0]
            ax_boxplot = axes[1]
        else:
            ax_kdeplot = axes[i, 0]
            ax_boxplot = axes[i, 1]

        # KDE Plot for clients who didn't pay the loan
        sns.kdeplot(data=df[df[target_col] == 0][col], ax=ax_kdeplot, label="Paid", color='blue')
        ax_kdeplot.set_title("KDE Plot - " + col)
        ax_kdeplot.set_xlabel(col)

        # KDE Plot for clients who paid the loan
        
        sns.kdeplot(data=df[df[target_col] == 1][col], ax=ax_kdeplot, label="Not Paid", color='red')
        # Boxplot for both groups
        sns.boxplot(data=df, x=target_col, y=col, ax=ax_boxplot, palette={1: 'red', 0: 'blue'})
        ax_boxplot.set_title("Box Plot - " + col)
        ax_boxplot.set_xlabel(target_col)
        ax_boxplot.set_ylabel(col)
        
        # Create a separate legend for the box plot
        handles, labels = ax_boxplot.get_legend_handles_labels()
        legend_labels = ["Paid", "Not Paid"]
    
        # Create custom artists for the legend
        custom_legend = [plt.Line2D([0], [0], marker='o', color='w', markerfacecolor=color,
                                    markersize=8, label=label) for color,
                         label in zip(['blue', 'red'], legend_labels)]
    
        #plt.legend(handles=custom_legend, loc='upper right')
    
        if i == i:
            ax_kdeplot.legend()
            plt.legend(handles=custom_legend)
    plt.tight_layout()
    plt.show()
    
    
def display_circles(
    pcs, n_comp, pca, axis_ranks, labels=None, label_rotation=0, lims=None
):
    """
    Trace les cercles de corrélations
    """
    for (
        d1,
        d2,
    ) in (
        axis_ranks
    ):  # On affiche les 3 premiers plans factoriels, donc les 6 premières composantes
        if d2 < n_comp:
            # initialisation de la figure
            fig, ax = plt.subplots(figsize=(5, 5))

            # détermination des limites du graphique
            if lims is not None:
                xmin, xmax, ymin, ymax = lims
            elif pcs.shape[1] < 30:
                xmin, xmax, ymin, ymax = -1, 1, -1, 1
            else:
                xmin, xmax, ymin, ymax = (
                    min(pcs[d1, :]),
                    max(pcs[d1, :]),
                    min(pcs[d2, :]),
                    max(pcs[d2, :]),
                )

            # affichage des flèches
            # s'il y a plus de 30 flèches, on n'affiche pas le triangle à leur extrémité
            if pcs.shape[1] < 30:
                plt.quiver(
                    np.zeros(pcs.shape[1]),
                    np.zeros(pcs.shape[1]),
                    pcs[d1, :],
                    pcs[d2, :],
                    angles="xy",
                    scale_units="xy",
                    scale=1,
                    color="grey",
                )
                # (voir la doc : https://matplotlib.org/api/_as_gen/matplotlib.pyplot.quiver.html)
            else:
                lines = [[[0, 0], [x, y]] for x, y in pcs[[d1, d2]].T]
                ax.add_collection(
                    LineCollection(lines, axes=ax, alpha=0.1, color="black")
                )

            # affichage des noms des variables
            if labels is not None:
                for i, (x, y) in enumerate(pcs[[d1, d2]].T):
                    if x >= xmin and x <= xmax and y >= ymin and y <= ymax:
                        plt.text(
                            x,
                            y,
                            labels[i],
                            fontsize="14",
                            ha="center",
                            va="center",
                            rotation=label_rotation,
                            color="black",
                            alpha=0.5,
                        )

            # affichage du cercle
            circle = plt.Circle((0, 0), 1, facecolor="none", edgecolor="b")
            plt.gca().add_artist(circle)

            # définition des limites du graphique
            plt.xlim(xmin, xmax)
            plt.ylim(ymin, ymax)

            # affichage des lignes horizontales et verticales
            plt.plot([-1, 1], [0, 0], color="grey", ls="--")
            plt.plot([0, 0], [-1, 1], color="grey", ls="--")

            # nom des axes, avec le pourcentage d'inertie expliqué
            plt.xlabel(
                "F{} ({}%)".format(
                    d1 + 1, round(100 * pca.explained_variance_ratio_[d1], 1)
                )
            )
            plt.ylabel(
                "F{} ({}%)".format(
                    d2 + 1, round(100 * pca.explained_variance_ratio_[d2], 1)
                )
            )

            plt.axis("square")
            plt.grid(True)
            plt.title("Cercle des corrélations (F{} et F{})".format(d1 + 1, d2 + 1))
            plt.show(block=False)
            plt.show()


def display_factorial_planes(
    X_projected,
    n_comp,
    pca,
    axis_ranks,
    couleurs=None,
    labels=None,
    n_cols=3,
    alpha=1,
    illustrative_var=None,
    lab_on=True,
    size=10,
):
    for i, (d1, d2) in enumerate(axis_ranks):
        if d2 < n_comp:
            # initialisation de la figure
            fig = plt.figure(figsize=(5, 5))

            # affichage des points
            if illustrative_var is None:
                plt.scatter(X_projected[:, d1], X_projected[:, d2], alpha=alpha, s=size)
            else:
                illustrative_var = np.array(illustrative_var)
                label_patches = []
                colors = couleurs
                i = 0

                for value in np.unique(illustrative_var):
                    selected = np.where(illustrative_var == value)
                    plt.scatter(
                        X_projected[selected, d1],
                        X_projected[selected, d2],
                        alpha=alpha,
                        label=value,
                        c=colors[i],
                    )
                    label_patch = mpatches.Patch(color=colors[i], label=value)
                    label_patches.append(label_patch)
                    i += 1
                    ax.legend(
                        handles=label_patches,
                        bbox_to_anchor=(1.05, 1),
                        loc=2,
                        borderaxespad=0.0,
                        facecolor="white",
                    )
                plt.legend()

            # affichage des labels des points
            if labels is not None and lab_on:
                for i, (x, y) in enumerate(X_projected[:, [d1, d2]]):
                    plt.text(x, y, labels[i], fontsize="14", ha="center", va="center")

            # détermination des limites du graphique
            boundary = np.max(np.abs(X_projected[:, [d1, d2]])) * 1.1
            plt.xlim([-boundary, boundary])
            plt.ylim([-boundary, boundary])

            # affichage des lignes horizontales et verticales
            plt.plot([-100, 100], [0, 0], color="grey", ls="--")
            plt.plot([0, 0], [-100, 100], color="grey", ls="--")

            # nom des axes, avec le pourcentage d'inertie expliqué
            plt.xlabel(
                "F{} ({}%)".format(
                    d1 + 1, round(100 * pca.explained_variance_ratio_[d1], 1)
                )
            )
            plt.ylabel(
                "F{} ({}%)".format(
                    d2 + 1, round(100 * pca.explained_variance_ratio_[d2], 1)
                )
            )

            plt.title(
                "Projection des individus (sur F{} et F{})".format(d1 + 1, d2 + 1)
            )
            plt.show(block=False)
            # plt.grid(False)


def display_screen_plot(pca):
    taux_var_exp = pca.explained_variance_ratio_
    scree = pca.explained_variance_ratio_ * 100
    plt.bar(np.arange(len(scree)) + 1, scree)
    ax1 = plt.gca()
    plt.plot(np.arange(len(scree)) + 1, scree.cumsum(), c="red", marker="o")
    plt.axhline(y=90, color="r")
    plt.text(2, 92, ">90%", color="r", fontsize=10)
    plt.xlabel("rang de l'axe d'inertie")
    plt.ylabel("pourcentage d'inertie")
    for i, p in enumerate(ax1.patches):
        ax1.text(
            p.get_width() / 5 + p.get_x(),
            p.get_height() + p.get_y() + 0.3,
            "{:.0f}%".format(taux_var_exp[i] * 100),
            fontsize=8,
            color="k",
        )
    plt.title("Eboulis des valeurs propres")
    plt.show(block=False)
    