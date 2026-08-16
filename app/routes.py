from flask import Blueprint, render_template

main = Blueprint("main", __name__)

@main.route("/")
def home():
    return render_template("home.html")

@main.route("/example")
def example():
    return render_template("example.html")

@main.route("/example2")
def example2():
    return render_template("example2.html")