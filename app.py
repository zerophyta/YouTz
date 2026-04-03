from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS
from flask_jwt_extended import JWTManager, jwt_required, create_access_token, get_jwt_identity
from flask_socketio import SocketIO, emit, disconnect
from sqlalchemy import create_engine, Column, Integer, String, Text, Boolean, DateTime, ForeignKey
from sqlalchemy.orm import sessionmaker, relationship, declarative_base
from sqlalchemy.sql import func
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
import os
import jwt
import datetime

app = Flask(__name__)
CORS(app)
app.config['JWT_SECRET_KEY'] = os.environ.get('JWT_SECRET', 'change-this-secret')
app.config['UPLOAD_FOLDER'] = 'uploads'
jwt_manager = JWTManager(app)
socketio = SocketIO(app, cors_allowed_origins="*")

# Database setup
DATABASE_URL = os.environ.get('DATABASE_URL', 'sqlite:///youtz.db')
engine = create_engine(DATABASE_URL, echo=False)
Session = sessionmaker(bind=engine)
Base = declarative_base()

class User(Base):
    __tablename__ = 'users'
    id = Column(Integer, primary_key=True)
    name = Column(String(255), nullable=False)
    email = Column(String(255), unique=True, nullable=False)
    password_hash = Column(String(255), nullable=False)
    bio = Column(Text)
    avatar = Column(String(255))
    created_at = Column(DateTime, default=func.now())

class Post(Base):
    __tablename__ = 'posts'
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey('users.id'), nullable=False)
    video_url = Column(String(255), nullable=False)
    caption = Column(Text)
    created_at = Column(DateTime, default=func.now())
    user = relationship('User')

class Follow(Base):
    __tablename__ = 'follows'
    follower_id = Column(Integer, ForeignKey('users.id'), primary_key=True)
    followee_id = Column(Integer, ForeignKey('users.id'), primary_key=True)

class Like(Base):
    __tablename__ = 'likes'
    user_id = Column(Integer, ForeignKey('users.id'), primary_key=True)
    post_id = Column(Integer, ForeignKey('posts.id'), primary_key=True)

class Comment(Base):
    __tablename__ = 'comments'
    id = Column(Integer, primary_key=True)
    post_id = Column(Integer, ForeignKey('posts.id'), nullable=False)
    user_id = Column(Integer, ForeignKey('users.id'), nullable=False)
    text = Column(Text, nullable=False)
    created_at = Column(DateTime, default=func.now())
    user = relationship('User')

class Share(Base):
    __tablename__ = 'shares'
    id = Column(Integer, primary_key=True)
    post_id = Column(Integer, ForeignKey('posts.id'), nullable=False)
    user_id = Column(Integer, ForeignKey('users.id'), nullable=False)
    created_at = Column(DateTime, default=func.now())

class Notification(Base):
    __tablename__ = 'notifications'
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey('users.id'), nullable=False)
    type = Column(String(50))  # 'like', 'comment', 'follow', 'share'
    from_user_id = Column(Integer, ForeignKey('users.id'), nullable=False)
    post_id = Column(Integer, ForeignKey('posts.id'))
    read = Column(Boolean, default=False)
    created_at = Column(DateTime, default=func.now())
    from_user = relationship('User', foreign_keys=[from_user_id])

Base.metadata.create_all(engine)

connected_clients = {}  # user_id -> list of sids

@socketio.on('connect')
def handle_connect():
    print('Client connected')

@socketio.on('disconnect')
def handle_disconnect():
    for user_id, sids in connected_clients.items():
        if request.sid in sids:
            sids.remove(request.sid)
            break

@socketio.on('auth')
def handle_auth(data):
    try:
        token = data['token']
        payload = jwt.decode(token, app.config['JWT_SECRET_KEY'], algorithms=['HS256'])
        user_id = payload['id']
        if user_id not in connected_clients:
            connected_clients[user_id] = []
        connected_clients[user_id].append(request.sid)
    except:
        disconnect()

def broadcast_notification(user_id, notification):
    if user_id in connected_clients:
        for sid in connected_clients[user_id]:
            socketio.emit('notification', notification, to=sid)

@app.route('/api/register', methods=['POST'])
def register():
    data = request.get_json()
    name = data.get('name')
    email = data.get('email')
    password = data.get('password')
    if not all([name, email, password]):
        return jsonify({'error': 'name, email, password required'}), 400

    session = Session()
    existing = session.query(User).filter_by(email=email).first()
    if existing:
        session.close()
        return jsonify({'error': 'email already exists'}), 409

    password_hash = generate_password_hash(password)
    user = User(name=name, email=email, password_hash=password_hash)
    session.add(user)
    session.commit()
    user_id = user.id
    session.close()

    token = create_access_token(identity={'id': user_id, 'email': email}, expires_delta=datetime.timedelta(days=7))
    return jsonify({'token': token, 'user': {'id': user_id, 'name': name, 'email': email}})

@app.route('/api/login', methods=['POST'])
def login():
    data = request.get_json()
    email = data.get('email')
    password = data.get('password')
    if not all([email, password]):
        return jsonify({'error': 'email/password required'}), 400

    session = Session()
    user = session.query(User).filter_by(email=email).first()
    if not user or not check_password_hash(user.password_hash, password):
        session.close()
        return jsonify({'error': 'invalid credentials'}), 401

    token = create_access_token(identity={'id': user.id, 'email': user.email}, expires_delta=datetime.timedelta(days=7))
    session.close()
    return jsonify({'token': token, 'user': {'id': user.id, 'name': user.name, 'email': user.email}})

@app.route('/api/posts', methods=['POST'])
@jwt_required()
def create_post():
    user_id = get_jwt_identity()['id']
    caption = request.form.get('caption', '').strip()
    file = request.files.get('video')
    if file:
        filename = secure_filename(file.filename)
        filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
        file.save(filepath)
        video_url = filepath
    else:
        video_url = request.form.get('videoUrl')
    if not video_url:
        return jsonify({'error': 'video file or videoUrl required'}), 400

    session = Session()
    post = Post(user_id=user_id, video_url=video_url, caption=caption)
    session.add(post)
    session.commit()
    post_id = post.id
    session.close()
    return jsonify({'id': post_id, 'user_id': user_id, 'video_url': video_url, 'caption': caption, 'created_at': post.created_at.isoformat()})

@app.route('/api/posts/feed', methods=['GET'])
@jwt_required()
def get_feed():
    user_id = get_jwt_identity()['id']
    session = Session()
    following = session.query(Follow.followee_id).filter_by(follower_id=user_id).all()
    feed_user_ids = [user_id] + [f[0] for f in following]
    posts = session.query(Post).filter(Post.user_id.in_(feed_user_ids)).order_by(Post.created_at.desc()).limit(50).all()

    enriched = []
    for post in posts:
        likes_count = session.query(Like).filter_by(post_id=post.id).count()
        comments = session.query(Comment).filter_by(post_id=post.id).order_by(Comment.created_at).all()
        shares_count = session.query(Share).filter_by(post_id=post.id).count()
        liked = session.query(Like).filter_by(user_id=user_id, post_id=post.id).first() is not None
        enriched.append({
            'id': post.id,
            'user_id': post.user_id,
            'video_url': post.video_url,
            'caption': post.caption,
            'created_at': post.created_at.isoformat(),
            'user': {'id': post.user.id, 'name': post.user.name},
            'likes': likes_count,
            'comments': [{'id': c.id, 'user_id': c.user_id, 'text': c.text, 'created_at': c.created_at.isoformat(), 'user': {'id': c.user.id, 'name': c.user.name}} for c in comments],
            'shares': shares_count,
            'liked': liked
        })
    session.close()
    return jsonify(enriched)

@app.route('/api/posts/<int:post_id>/like', methods=['POST'])
@jwt_required()
def like_post(post_id):
    user_id = get_jwt_identity()['id']
    session = Session()
    like = session.query(Like).filter_by(user_id=user_id, post_id=post_id).first()
    if like:
        session.delete(like)
        liked = False
    else:
        like = Like(user_id=user_id, post_id=post_id)
        session.add(like)
        liked = True
        post = session.query(Post).filter_by(id=post_id).first()
        if post.user_id != user_id:
            notification = Notification(user_id=post.user_id, type='like', from_user_id=user_id, post_id=post_id)
            session.add(notification)
            session.commit()
            notif_data = {
                'id': notification.id,
                'type': 'like',
                'from_user': {'id': notification.from_user.id, 'name': notification.from_user.name},
                'post_id': post_id,
                'read': False
            }
            broadcast_notification(post.user_id, notif_data)
        else:
            session.commit()
    session.close()
    return jsonify({'liked': liked})

@app.route('/api/posts/<int:post_id>/comment', methods=['POST'])
@jwt_required()
def comment_post(post_id):
    user_id = get_jwt_identity()['id']
    data = request.get_json()
    text = data.get('text', '').strip()
    if not text:
        return jsonify({'error': 'comment text required'}), 400

    session = Session()
    comment = Comment(post_id=post_id, user_id=user_id, text=text)
    session.add(comment)
    session.commit()
    comment_id = comment.id

    post = session.query(Post).filter_by(id=post_id).first()
    if post.user_id != user_id:
        notification = Notification(user_id=post.user_id, type='comment', from_user_id=user_id, post_id=post_id)
        session.add(notification)
        session.commit()
        notif_data = {
            'id': notification.id,
            'type': 'comment',
            'from_user': {'id': notification.from_user.id, 'name': notification.from_user.name},
            'post_id': post_id,
            'read': False
        }
        broadcast_notification(post.user_id, notif_data)
    session.close()
    return jsonify({'id': comment_id, 'post_id': post_id, 'user_id': user_id, 'text': text, 'created_at': comment.created_at.isoformat()})

@app.route('/api/posts/<int:post_id>/share', methods=['POST'])
@jwt_required()
def share_post(post_id):
    user_id = get_jwt_identity()['id']
    session = Session()
    share = Share(post_id=post_id, user_id=user_id)
    session.add(share)
    session.commit()
    share_id = share.id
    session.close()
    return jsonify({'id': share_id, 'post_id': post_id, 'user_id': user_id, 'created_at': share.created_at.isoformat()})

@app.route('/api/users/<int:user_id>/follow', methods=['POST'])
@jwt_required()
def follow_user(user_id):
    follower_id = get_jwt_identity()['id']
    if user_id == follower_id:
        return jsonify({'error': 'invalid follow operation'}), 400

    session = Session()
    follow = session.query(Follow).filter_by(follower_id=follower_id, followee_id=user_id).first()
    if follow:
        session.close()
        return jsonify({'following': True})

    follow = Follow(follower_id=follower_id, followee_id=user_id)
    session.add(follow)
    notification = Notification(user_id=user_id, type='follow', from_user_id=follower_id)
    session.add(notification)
    session.commit()
    notif_data = {
        'id': notification.id,
        'type': 'follow',
        'from_user': {'id': notification.from_user.id, 'name': notification.from_user.name},
        'read': False
    }
    broadcast_notification(user_id, notif_data)
    session.close()
    return jsonify({'following': True})

@app.route('/api/users/<int:user_id>/unfollow', methods=['DELETE'])
@jwt_required()
def unfollow_user(user_id):
    follower_id = get_jwt_identity()['id']
    session = Session()
    follow = session.query(Follow).filter_by(follower_id=follower_id, followee_id=user_id).first()
    if follow:
        session.delete(follow)
        session.commit()
    session.close()
    return jsonify({'following': False})

@app.route('/api/users/<int:user_id>', methods=['GET'])
@jwt_required()
def get_user(user_id):
    session = Session()
    user = session.query(User).filter_by(id=user_id).first()
    if not user:
        session.close()
        return jsonify({'error': 'user not found'}), 404

    posts_count = session.query(Post).filter_by(user_id=user_id).count()
    followers_count = session.query(Follow).filter_by(followee_id=user_id).count()
    following_count = session.query(Follow).filter_by(follower_id=user_id).count()
    is_following = session.query(Follow).filter_by(follower_id=get_jwt_identity()['id'], followee_id=user_id).first() is not None
    session.close()
    return jsonify({
        'id': user.id,
        'name': user.name,
        'email': user.email,
        'bio': user.bio,
        'avatar': user.avatar,
        'posts_count': posts_count,
        'followers_count': followers_count,
        'following_count': following_count,
        'is_following': is_following
    })

@app.route('/api/users/<int:user_id>/posts', methods=['GET'])
@jwt_required()
def get_user_posts(user_id):
    current_user_id = get_jwt_identity()['id']
    session = Session()
    posts = session.query(Post).filter_by(user_id=user_id).order_by(Post.created_at.desc()).all()
    enriched = []
    for post in posts:
        likes_count = session.query(Like).filter_by(post_id=post.id).count()
        comments = session.query(Comment).filter_by(post_id=post.id).order_by(Comment.created_at).all()
        shares_count = session.query(Share).filter_by(post_id=post.id).count()
        liked = session.query(Like).filter_by(user_id=current_user_id, post_id=post.id).first() is not None
        enriched.append({
            'id': post.id,
            'user_id': post.user_id,
            'video_url': post.video_url,
            'caption': post.caption,
            'created_at': post.created_at.isoformat(),
            'likes': likes_count,
            'comments': [{'id': c.id, 'user_id': c.user_id, 'text': c.text, 'created_at': c.created_at.isoformat(), 'user': {'id': c.user.id, 'name': c.user.name}} for c in comments],
            'shares': shares_count,
            'liked': liked
        })
    session.close()
    return jsonify(enriched)

@app.route('/api/posts/trending', methods=['GET'])
@jwt_required()
def get_trending():
    user_id = get_jwt_identity()['id']
    thirty_days_ago = datetime.datetime.now() - datetime.timedelta(days=30)
    session = Session()
    posts = session.query(Post).filter(Post.created_at > thirty_days_ago).all()
    scored = []
    for post in posts:
        likes_count = session.query(Like).filter_by(post_id=post.id).count()
        comments_count = session.query(Comment).filter_by(post_id=post.id).count()
        shares_count = session.query(Share).filter_by(post_id=post.id).count()
        liked = session.query(Like).filter_by(user_id=user_id, post_id=post.id).first() is not None
        score = likes_count * 3 + comments_count * 5 + shares_count * 10
        scored.append({
            'id': post.id,
            'user_id': post.user_id,
            'video_url': post.video_url,
            'caption': post.caption,
            'created_at': post.created_at.isoformat(),
            'user': {'id': post.user.id, 'name': post.user.name},
            'likes': likes_count,
            'comments': [],
            'shares': shares_count,
            'liked': liked,
            'score': score
        })
    scored.sort(key=lambda x: x['score'], reverse=True)
    session.close()
    return jsonify(scored[:30])

@app.route('/api/posts/search', methods=['GET'])
@jwt_required()
def search_posts():
    q = request.args.get('q', '').strip()
    if not q:
        return jsonify([])

    user_id = get_jwt_identity()['id']
    session = Session()
    posts = session.query(Post).filter(Post.caption.like(f'%{q}%')).order_by(Post.created_at.desc()).limit(50).all()
    enriched = []
    for post in posts:
        likes_count = session.query(Like).filter_by(post_id=post.id).count()
        comments = session.query(Comment).filter_by(post_id=post.id).order_by(Comment.created_at).all()
        shares_count = session.query(Share).filter_by(post_id=post.id).count()
        liked = session.query(Like).filter_by(user_id=user_id, post_id=post.id).first() is not None
        enriched.append({
            'id': post.id,
            'user_id': post.user_id,
            'video_url': post.video_url,
            'caption': post.caption,
            'created_at': post.created_at.isoformat(),
            'user': {'id': post.user.id, 'name': post.user.name},
            'likes': likes_count,
            'comments': [{'id': c.id, 'user_id': c.user_id, 'text': c.text, 'created_at': c.created_at.isoformat(), 'user': {'id': c.user.id, 'name': c.user.name}} for c in comments],
            'shares': shares_count,
            'liked': liked
        })
    session.close()
    return jsonify(enriched)

@app.route('/api/notifications', methods=['GET'])
@jwt_required()
def get_notifications():
    user_id = get_jwt_identity()['id']
    session = Session()
    notifications = session.query(Notification).filter_by(user_id=user_id).order_by(Notification.created_at.desc()).limit(50).all()
    enriched = []
    for n in notifications:
        enriched.append({
            'id': n.id,
            'user_id': n.user_id,
            'type': n.type,
            'from_user_id': n.from_user_id,
            'post_id': n.post_id,
            'read': n.read,
            'created_at': n.created_at.isoformat(),
            'from_user': {'id': n.from_user.id, 'name': n.from_user.name}
        })
    session.close()
    return jsonify(enriched)

@app.route('/api/notifications/<int:notif_id>/read', methods=['POST'])
@jwt_required()
def mark_notification_read(notif_id):
    session = Session()
    notification = session.query(Notification).filter_by(id=notif_id).first()
    if notification:
        notification.read = True
        session.commit()
    session.close()
    return jsonify({'success': True})

@app.route('/uploads/<filename>')
def uploaded_file(filename):
    return send_from_directory(app.config['UPLOAD_FOLDER'], filename)

if __name__ == '__main__':
    os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)
    socketio.run(app, host='0.0.0.0', port=4000, debug=True)