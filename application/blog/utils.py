import requests
import bcrypt
import random
import string
from math import ceil
from datetime import datetime, timedelta
from flask import flash, render_template, abort, request
from flask_login import current_user
from bs4 import BeautifulSoup
from application.extensions.mongo import db_users, db_posts, db_comments
from application.extensions.log import logger
from application.config import ENV, RECAPTCHA_SECRET


class HTML_Formatter:
    def __init__(self, html):

        self.__soup = BeautifulSoup(html, "html.parser")

    def add_padding(self):

        # Find all tags in the HTML
        # except figure and img tag
        tags = self.__soup.find_all(
            lambda tag: tag.name not in ["figure", "img"], recursive=False
        )

        # Add padding to each tag
        for tag in tags:
            current_style = tag.get("style", "")
            new_style = f"{current_style} padding-top: 10px; padding-bottom: 10px; "
            tag["style"] = new_style

        return self

    def change_heading_font(self):

        # Modify the style attribute for each heading tag
        headings = self.__soup.find_all(["h1", "h2", "h3", "h4", "h5", "h6"])

        # Modify the style attribute for each heading tag
        for heading in headings:
            current_style = heading.get("style", "")
            new_style = f"{current_style} font-family: 'Ubuntu', 'Arial', sans-serif;;"
            heading["style"] = new_style

        return self

    def modify_figure(self, max_width="90%"):

        imgs = self.__soup.find_all(["img"])

        # center image and modify size
        for img in imgs:
            current_style = img.get("style", "")
            new_style = f"{current_style} display: block; margin: 0 auto; max-width: {max_width}; min-width: 30% ;height: auto;"
            img["style"] = new_style

        captions = self.__soup.find_all(["figcaption"])

        # center caption
        for caption in captions:
            current_style = caption.get("style", "")
            new_style = f"{current_style} text-align: center"
            caption["style"] = new_style

        return self

    def to_string(self):

        return str(self.__soup)

    def to_blogpost(self):

        blogpost = self.add_padding().change_heading_font().modify_figure().to_string()

        return blogpost

    def to_about(self):

        about = self.add_padding().modify_figure(max_width="50%").to_string()

        return about


def create_user(request: request) -> str:

    reg_form = request.form.to_dict()
    # registeration
    # with unique email, username and blog name
    # make sure username has no space character
    reg_form["username"] = reg_form["username"].strip().replace(" ", "-")
    if db_users.login.exists("email", reg_form["email"]):
        flash("Email is already used. Please try another one.", category="error")
        logger.user.registration_failed(
            username=reg_form["username"],
            msg=f'email {reg_form["email"]} already used',
            request=request,
        )
        return render_template("register.html")

    if db_users.login.exists("username", reg_form["username"]):
        flash("Username is already used. Please try another one.", category="error")
        logger.user.registration_failed(
            username=reg_form["username"],
            msg=f'username {reg_form["username"]} already used',
            request=request,
        )
        return render_template("register.html")

    if db_users.info.exists("blogname", reg_form["blogname"]):
        flash("Blog name is already used. Please try another one.")
        logger.user.registration_failed(
            username=reg_form["username"],
            msg=f'blog name {reg_form["blogname"]} already used',
            request=request,
        )
        return render_template("register.html")

    hashed_pw = bcrypt.hashpw(reg_form["password"].encode("utf-8"), bcrypt.gensalt(12))
    hashed_pw = hashed_pw.decode("utf-8")

    new_user_login = {
        "username": reg_form["username"],
        "email": reg_form["email"],
        "password": hashed_pw,
    }

    new_user_info = {
        "username": reg_form["username"],
        "blogname": reg_form["blogname"],
        "email": reg_form["email"],
        "posts_count": 0,
        "banner_url": "",
        "profile_img_url": "",
        "short_bio": "",
        "social_links": [],
        "change_log_enabled": False,
        "portfolio_enabled": True,
        "created_at": get_today(),
    }

    new_user_about = {"username": reg_form["username"], "about": ""}

    db_users.login.insert_one(new_user_login)
    db_users.info.insert_one(new_user_info)
    db_users.about.insert_one(new_user_about)

    return reg_form["username"]


def create_comment(post_uid, request):

    new_comment = {}
    new_comment["created_at"] = get_today()
    new_comment["post_uid"] = post_uid
    new_comment["comment"] = request.form.get("comment")
    alphabet = string.ascii_lowercase + string.digits
    comment_uid = "".join(random.choices(alphabet, k=8))
    while db_comments.comment.exists("comment_uid", comment_uid):
        comment_uid = "".join(random.choices(alphabet, k=8))
    new_comment["comment_uid"] = comment_uid

    if current_user.is_authenticated:
        commenter = db_users.info.find_one({"username": current_user.username})
        new_comment["name"] = current_user.username
        new_comment["email"] = commenter["email"]
        new_comment["profile_link"] = f"/{current_user.username}/about"
        new_comment["profile_pic"] = f"/{current_user.username}/get-profile-pic"

    else:
        new_comment["name"] = f'{request.form.get("name")} (Visitor)'
        new_comment["email"] = request.form.get("email")
        new_comment["profile_pic"] = "/static/img/visitor.png"
        if new_comment["email"]:
            new_comment["profile_link"] = f'mailto:{new_comment["email"]}'
        else:
            new_comment["profile_link"] = ""

    db_comments.comment.insert_one(new_comment)


def all_tags_from_user(username):

    result = db_posts.info.find({"author": username, "archived": False})
    tags_dict = {}
    for post in result:
        post_tags = post["tags"]
        for tag in post_tags:
            if tag not in tags_dict:
                tags_dict[tag] = 1
            else:
                tags_dict[tag] += 1

    sorted_tags_key = sorted(tags_dict, key=tags_dict.get, reverse=True)
    sorted_tags = {}
    for key in sorted_tags_key:
        sorted_tags[key] = tags_dict[key]

    return sorted_tags


def is_comment_verified(token):

    payload = {"secret": RECAPTCHA_SECRET, "response": token}
    r = requests.post("https://www.google.com/recaptcha/api/siteverify", params=payload)
    response = r.json()

    if response["success"]:
        return True
    return False


class Pagination:
    def __init__(self, username, current_page, posts_per_page):

        self.__allow_previous_page = False
        self.__allow_next_page = False
        self.__current_page = current_page

        # set up for pagination
        num_not_archieved = db_posts.info.count_documents(
            {"author": username, "archived": False}
        )
        if num_not_archieved == 0:
            max_page = 1
        else:
            max_page = ceil(num_not_archieved / posts_per_page)

        if current_page > max_page:
            # not a legal page number
            abort(404)

        if current_page * posts_per_page < num_not_archieved:
            self.__allow_previous_page = True

        if current_page > 1:
            self.__allow_next_page = True

    @property
    def is_previous_page_allowed(self):
        return self.__allow_previous_page

    @property
    def is_next_page_allowed(self):
        return self.__allow_next_page

    @property
    def current_page(self):
        return self.__current_page


def get_today():

    if ENV == "debug":
        today = datetime.now()
    elif ENV == "prod":
        today = datetime.now() + timedelta(hours=8)
    return today
