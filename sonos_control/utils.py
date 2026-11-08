# utils.py

from spotipy.oauth2 import SpotifyOAuth
from django.conf import settings
import qrcode
import io
import base64

def generate_auth_url(state):
    auth_manager = SpotifyOAuth(
        client_id=settings.SPOTIFY_CLIENT_ID,
        client_secret=settings.SPOTIFY_CLIENT_SECRET,
        redirect_uri=settings.SPOTIFY_REDIRECT_URI,
        scope=settings.SPOTIFY_SCOPE,
        state=state,
        show_dialog=True,
    )
    auth_url = auth_manager.get_authorize_url()
    return auth_url

def generate_qr_code(auth_url):
    qr = qrcode.QRCode(
        version=1,
        box_size=10,
        border=4,
    )
    qr.add_data(auth_url)
    qr.make(fit=True)
    img = qr.make_image(fill='black', back_color='white')
    buffer = io.BytesIO()
    img.save(buffer, format='PNG')
    img_str = base64.b64encode(buffer.getvalue()).decode()
    return img_str
