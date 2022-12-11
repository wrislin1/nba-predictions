from flask import Flask, render_template, request, redirect, url_for, flash,session
from os.path import relpath
import pandas as pd
import numpy as np
from threading import Thread, Event
import time
from datetime import datetime
import matplotlib.pyplot as plt
from scipy.stats import norm
from mpl_toolkits import mplot3d
from sklearn.neighbors import KNeighborsRegressor
from sklearn.model_selection import train_test_split
from sklearn import preprocessing
from sklearn.neighbors import KNeighborsClassifier
import seaborn as sns
from sklearn.cluster import KMeans
from sklearn.decomposition import PCA
from sklearn.model_selection import ParameterGrid
from sklearn import metrics
from joblib import dump, load
from math import floor, ceil

app = Flask(__name__)

@app.context_processor
def utility_processor():
    return dict(floor=floor, ceil = ceil, int = int)

@app.route("/")
def index():
    cumeGameAvgs = pd.read_csv('venv/static/data/nba_predictions_2022_2023_new.csv', index_col=0)
    nbaEras = pd.read_csv('venv/static/data/nba-eras.csv', index_col=0)
    nbaList = [row.to_dict() for i, row in cumeGameAvgs.iterrows()]
    nbaEras = [row.to_dict() for i, row in nbaEras.iterrows()]
    imgs = getImages()
    return render_template('dashboard.html', preds = nbaList, imgs = imgs, eras = nbaEras)

def getImages():
    imgList = []
    columns = ['HOME_PTS', 'HOME_FGM', 'HOME_FGA','HOME_FG3M', 'HOME_FG3A',
               'HOME_FTM', 'HOME_FTA','HOME_PF', 'AWAY_PTS',
               'AWAY_FGM', 'AWAY_FGA', 'AWAY_FG3M', 'AWAY_FG3A',
               'AWAY_FTM','AWAY_FTA', 'AWAY_PF', 'ATTENDANCE']

    for column in columns:
        imgList.append(f"/static/img/{column}_era_averages.png")
    return imgList