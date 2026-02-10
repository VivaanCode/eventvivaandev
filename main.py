from flask import Flask, render_template, redirect, request
import os
import psycopg2

db_url = os.getenv("DATABASE_URL")
app = Flask('app', static_folder="static", template_folder="templates")

@app.route("/")
def index():
    return render_template("index.html")

    

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=8080)
