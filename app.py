import os
import requests
from flask import Flask, request, redirect, url_for, session, flash, send_from_directory, render_template
from flask_sqlalchemy import SQLAlchemy
from werkzeug.security import generate_password_hash, check_password_hash

app = Flask(__name__)
app.config['SECRET_KEY'] = 'secret-key'
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///media_hub.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

# Папка для сохранения скачанных постеров из стороннего API
UPLOAD_FOLDER = os.path.join(os.getcwd(), 'static_uploads')
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

db = SQLAlchemy(app)

# Ключ для работы со сторонним API
OMDB_API_KEY = '608c254f'


class User(db.Model):
    # Модель пользователя для регистрации и авторизации
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    password_hash = db.Column(db.String(120), nullable=False)
    movies = db.relationship('MovieItem', backref='author', lazy=True)


class MovieItem(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(150), nullable=False)
    year = db.Column(db.String(20), nullable=True)
    director = db.Column(db.String(150), nullable=True)
    genre = db.Column(db.String(100), nullable=True)
    plot = db.Column(db.Text, nullable=True)
    image_filename = db.Column(db.String(200), nullable=True)
    imdb_id = db.Column(db.String(30), nullable=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)


@app.route('/')
def index():
    # Главная страница со списком всех импортированных фильмов
    movies = MovieItem.query.all()
    return render_template('index.html', movies=movies)


@app.route('/search-api', methods=['GET'])
def search_api_page():
    # Страница поиска. Получает список фильмов и обрезает до 5 результатов
    if 'user_id' not in session:
        flash('Пожалуйста, авторизуйтесь для использования поиска!', 'error')
        return redirect(url_for('login'))

    query = request.args.get('query', '').strip()
    sliced_results = []

    if query:
        search_url = f"http://www.omdbapi.com/?apikey={OMDB_API_KEY}&s={query}"

        try:
            response = requests.get(search_url, timeout=5)
            if response.status_code == 200:
                data = response.json()
                if data.get('Response') == 'True':
                    full_list = data.get('Search', [])
                    sliced_results = full_list[:5]
            else:
                flash('Ошибка при обращении к стороннему серверу.', 'error')
        except requests.RequestException:
            flash('Внешний сервер API недоступен. Проверьте интернет.', 'error')

    return render_template('search.html', query=query, search_results=sliced_results)


@app.route('/add-from-api', methods=['POST'])
def add_movie_from_api():
    if 'user_id' not in session:
        flash('Сессия истекла.', 'error')
        return redirect(url_for('login'))

    imdb_id = request.form.get('imdb_id')

    duplicate = MovieItem.query.filter_by(imdb_id=imdb_id, user_id=session['user_id']).first()
    if duplicate:
        flash('Этот фильм уже есть в вашей коллекции!', 'info')
        return redirect(url_for('index'))

    detail_url = f"http://www.omdbapi.com/?apikey={OMDB_API_KEY}&i={imdb_id}"

    try:
        res = requests.get(detail_url, timeout=5)
        if res.status_code != 200:
            flash('Не удалось загрузить детальные данные из API.', 'error')
            return redirect(url_for('search_api_page'))
        movie_data = res.json()
    except requests.RequestException:
        flash('Ошибка сети при импорте данных.', 'error')
        return redirect(url_for('search_api_page'))

    title = movie_data.get('Title', 'Неизвестно')
    year = movie_data.get('Year', 'Неизвестно')
    director = movie_data.get('Director', 'Неизвестно')
    genre = movie_data.get('Genre', 'Неизвестно')
    plot = movie_data.get('Plot', 'Описание отсутствует.')
    poster_url = movie_data.get('Poster')

    saved_filename = None
    if poster_url and poster_url != 'N/A':
        try:
            img_response = requests.get(poster_url, timeout=5)
            if img_response.status_code == 200:
                saved_filename = f"movie_{imdb_id}.jpg"
                file_path = os.path.join(app.config['UPLOAD_FOLDER'], saved_filename)

                with open(file_path, 'wb') as f:
                    f.write(img_response.content)
        except Exception as e:
            print(f"Ошибка сохранения файла картинки: {e}")

    new_movie = MovieItem(
        title=title,
        year=year,
        director=director,
        genre=genre,
        plot=plot,
        image_filename=saved_filename,
        imdb_id=imdb_id,
        user_id=session['user_id']
    )

    db.session.add(new_movie)
    db.session.commit()

    flash(f'Фильм "{title}" успешно добавлен!', 'success')
    return redirect(url_for('index'))


@app.route('/delete/<int:movie_id>')
def delete_movie(movie_id):
    if 'user_id' not in session:
        return redirect(url_for('login'))

    movie = MovieItem.query.get_or_404(movie_id)

    if movie.user_id != session['user_id']:
        flash('Вы не можете удалять чужие карточки!', 'error')
        return redirect(url_for('index'))

    if movie.image_filename:
        try:
            os.remove(os.path.join(app.config['UPLOAD_FOLDER'], movie.image_filename))
        except FileNotFoundError:
            pass

    db.session.delete(movie)
    db.session.commit()
    flash('Фильм удален из локальной базы.', 'success')
    return redirect(url_for('index'))


@app.route('/uploads/<filename>')
def custom_uploads(filename):
    return send_from_directory(app.config['UPLOAD_FOLDER'], filename)


@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        username = request.form.get('username').strip()
        password = request.form.get('password')

        if not username or not password:
            flash('Заполните все поля формы!', 'error')
            return render_template('register.html')

        if User.query.filter_by(username=username).first():
            flash('Имя пользователя уже занято!', 'error')
            return render_template('register.html')

        user = User(username=username, password_hash=generate_password_hash(password))
        db.session.add(user)
        db.session.commit()
        flash('Регистрация завершена! Войдите.', 'success')
        return redirect(url_for('login'))
    return render_template('register.html')


@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form.get('username').strip()
        password = request.form.get('password')
        user = User.query.filter_by(username=username).first()
        if user and check_password_hash(user.password_hash, password):
            session['user_id'] = user.id
            session['username'] = user.username
            flash('Вход выполнен успешно!', 'success')
            return redirect(url_for('index'))
        else:
            flash('Неверный логин или пароль!', 'error')
    return render_template('login.html')


@app.route('/logout')
def logout():
    session.clear()
    flash('Сессия завершена.', 'info')
    return redirect(url_for('index'))


if __name__ == '__main__':
    with app.app_context():
        db.create_all()
    print('Локальный веб-сервер успешно инициализирован.')
    app.run(debug=True, host='127.0.0.1', port=5000)
