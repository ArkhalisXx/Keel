"""Generate local Docker configuration without printing secrets or overwriting a file."""
from pathlib import Path
import secrets

root = Path(__file__).resolve().parent.parent
target = root / '.env'
text = (root / '.env.example').read_text()
text = text.replace('DJANGO_SECRET_KEY=\n', 'DJANGO_SECRET_KEY=' + secrets.token_urlsafe(50) + '\n')
text = text.replace('POSTGRES_PASSWORD=\n', 'POSTGRES_PASSWORD=' + secrets.token_urlsafe(32) + '\n')
try:
    with target.open('x') as file:
        file.write(text)
    target.chmod(0o600)
    print('Created private .env settings. Add your API key there if needed; never commit this file.')
except FileExistsError:
    print('Existing .env preserved. Compare names with .env.example if settings are missing.')
print('Start: docker compose up --build -d')
print('Admin account: docker compose exec web python manage.py createsuperuser')
print('Stop: docker compose down (preserves volumes). Never use --volumes unless you intend to erase data.')
print('Docker uses a separate PostgreSQL database. Existing local SQLite data is not copied automatically.')
