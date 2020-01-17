import mimetypes
import os
import pathlib
import random
import re
import shutil
import sys
import threading
import urllib
import webbrowser

from flask import request

from urllib.parse import unquote, quote

import flask
import trueskill
import xattr

from pprint import pprint, pformat

app = flask.Flask(__name__)

APP_URL = 'http://127.0.0.1:5000/'
RANK_MULTIPLIER = 1000
TEMPLATE = "img-mm.tpl"
MU_XATTR = "ts.mu"
SIGMA_XATTR = "ts.sigma"
SUPPORTED_EXTS = [
    ".bmp"
    ".gif",
    ".jpeg",
    ".jpg",
    ".png",
    ".webp",
]

eligible_files = []

def rank(rating):
    rank = str(
        (50 * RANK_MULTIPLIER) - round(
            (rating.mu - (3 * rating.sigma)) * RANK_MULTIPLIER
        )
    )
    return rank

def get_file(filename):
    file_xattr = xattr.xattr(filename)
    try:
        mu = file_xattr.get(MU_XATTR)
        mu = float(mu.decode("UTF8"))
    except OSError:
        mu = None
    try:
        sigma = file_xattr.get(SIGMA_XATTR)
        sigma = float(sigma.decode("UTF8"))
    except OSError:
        sigma = None
    previous_rating = False
    if mu is not None or sigma is not None:
        previous_rating = True
    rating = trueskill.Rating(mu=mu, sigma=sigma)
    return {
        "filename": quote(filename),
        "mtime": os.path.getmtime(filename),
        "previous_rating": previous_rating,
        "rating": rating,
        "rank": rank(rating)
    }

def update_file(filename, rating, rm=False):
    filename = unquote(filename)
    file_xattr = xattr.xattr(filename)
    file_xattr.set(MU_XATTR, str(rating.mu).encode('UTF8'))
    file_xattr.set(SIGMA_XATTR, str(rating.sigma).encode('UTF8'))
    path = pathlib.PurePath(filename)
    suffix = re.split('^(\d+R)\s+?', path.name)[-1]
    new_name = "%sR %s" % (rank(rating), suffix)
    if not rm:
        new_filename = str(path.parent.joinpath(new_name))
    else:
        path = pathlib.PurePath("/tmp")
        new_filename = str(path.joinpath(new_name))
    shutil.move(filename, new_filename)
    new_file = get_file(new_filename)
    for i, file in enumerate(eligible_files):
        if file["filename"] == quote(filename):
            if not rm:
                eligible_files[i] = new_file
            else:
                del(eligible_files[i])
    return new_file

def update_rankings(win, lose, rm=False):
    try:
        win_file = get_file(win)
        lose_file = get_file(lose)
    except FileNotFoundError:
        # File has gone. User might have refreshed page after file was renamed.
        # Either way, no way to recover, so take no action.
        return
    win_rating, lose_rating = \
        trueskill.rate_1vs1(win_file["rating"], lose_file["rating"])
    print("Before:")
    print("  Win:  " + rank(win_file["rating"]) + " " + win_file["filename"])
    print("  Lose: " + rank(lose_file["rating"]) + " " + lose_file["filename"])
    print("After:")
    print("  Win:  " + rank(win_rating) + " " + win_file["filename"])
    print("  Lose: " + rank(lose_rating) + " " + lose_file["filename"])
    win_file = update_file(win_file["filename"], win_rating)
    lose_file = update_file(lose_file["filename"], lose_rating, rm)
    if not rm:
        results = {"win": win_file, "lose": lose_file}
    else:
        results = {"win": win_file, "rm": lose_file}
    pprint(results)
    return results

def load():
    try:
        dir = sys.argv[1]
    except IndexError:
        print("You must specify a directory!")
        sys.exit(1)
    filenames = list(pathlib.Path(dir).rglob("*"))
    if len(filenames) < 2:
        print("Did not find images!")
        sys.exit(1)
    for filename in filenames:
        ext = filename.suffix.lower()
        if ext not in SUPPORTED_EXTS:
            continue
        # print("Loading: %s" % filename)
        eligible_files.append(get_file(str(filename)))

def get_candidates():
    # Select the file with the lowest sigma (confidence) as the starting
    # candidate
    highest_sigma_file = None
    for file_dict in eligible_files:
        if highest_sigma_file is None:
            highest_sigma_file = file_dict
            continue
        if file_dict['rating'].sigma > highest_sigma_file['rating'].sigma:
            highest_sigma_file = file_dict
            continue
    # Select the file with the closest rank
    closest_mu_files = []
    lowest_mu_difference = None
    for file_dict in eligible_files:
        # Don't pit an image against itself
        if file_dict["filename"] == highest_sigma_file["filename"]:
            continue
        mu_difference = abs(
            highest_sigma_file['rating'].mu - file_dict['rating'].mu)
        if len(closest_mu_files) == 0 or mu_difference == lowest_mu_difference:
            closest_mu_files.append(file_dict)
            lowest_mu_difference = mu_difference
            continue
        if mu_difference < lowest_mu_difference:
            closest_mu_files = [file_dict]
            lowest_mu_difference = mu_difference
            continue
    candidates = [highest_sigma_file, random.choice(closest_mu_files)]
    return candidates

@app.route('/img')
def img():
    filename = request.args.get('filename')
    img_file = open(filename, "rb")
    mimetype = mimetypes.guess_type(filename)[0]
    return flask.send_file(img_file, mimetype=mimetype)

@app.route('/')
def index():
    win = request.args.get('win')
    lose = request.args.get('lose')
    rm = request.args.get('rm')
    results = None
    if win is not None:
        if lose is not None:
            results = update_rankings(unquote(win), unquote(lose))
        if rm is not None:
            results = update_rankings(unquote(win), unquote(rm), rm=True)
    context = {
        "results": results,
        "candidates": get_candidates()
    }
    return flask.render_template(TEMPLATE, **context)

if __name__ == '__main__':
    load()
    # Prevent multiple browser windows being opened because of code reloading
    # when Flask debug is active
    if 'WERKZEUG_RUN_MAIN' not in os.environ:
        threading.Timer(1, lambda: webbrowser.open(APP_URL)).start()
    app.run()

