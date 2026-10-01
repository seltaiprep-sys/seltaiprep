# check_topic.py
from app import app, db
from sqlalchemy import text

with app.app_context():
    conn = db.engine.connect()
    result = conn.execute(text(
        "SELECT character_maximum_length "
        "FROM information_schema.columns "
        "WHERE table_name='ukvi_test_bank' AND column_name='topic'"
    ))
    length = result.scalar()
    conn.close()
    print(f"✓ topic column length: {length}")
    if length == 255:
        print("✓ Migration successful!")
    else:
        print("✗ Migration may have failed")