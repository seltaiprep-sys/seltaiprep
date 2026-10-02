"""
Migration script — Backfill disk files for existing FullTestVariant rows.

Usage:
    python migrate_full_test_variants.py
    python migrate_full_test_variants.py --variant 2    (only variant #2)
    python migrate_full_test_variants.py --force        (overwrite existing files)
    python migrate_full_test_variants.py --dry-run      (show what would happen)
"""

import os
import sys
import json
import shutil
import argparse

# ─── Ensure project root on path ────────────────────────────
PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from dotenv import load_dotenv
load_dotenv(os.path.join(PROJECT_ROOT, '.env'), encoding='utf-8-sig')

from app import app, db
from models import get_full_test_variant_model


def url_to_fs(url: str, static_root: str):
    """Convert /static/... URL to absolute filesystem path."""
    if not url or not isinstance(url, str):
        return None
    clean = url.split('?', 1)[0].strip()
    if not clean:
        return None
    if clean.startswith('/static/'):
        return os.path.join(static_root, clean[len('/static/'):])
    if clean.startswith('static/'):
        return os.path.join(os.path.dirname(static_root), clean)
    if clean.startswith('/'):
        return os.path.join(static_root, clean.lstrip('/'))
    candidate = os.path.join(static_root, clean)
    if os.path.exists(candidate):
        return candidate
    return clean


def copy_audio_to_variant(variant_id, audio_urls, static_root):
    """Copy audio files to variant folder, return new URLs."""
    if not audio_urls:
        return {}

    vdir = os.path.join(
        static_root, 'full_test_bank', f'variant_{variant_id:03d}'
    )
    os.makedirs(vdir, exist_ok=True)

    new_urls = {}
    for sec_key, sec_val in audio_urls.items():
        if isinstance(sec_val, dict):
            main_url = sec_val.get('main')
            rest = {k: v for k, v in sec_val.items() if k != 'main'}
        else:
            main_url = sec_val
            rest = {}

        if not main_url:
            continue

        src = url_to_fs(main_url, static_root)
        if not src or not os.path.exists(src):
            print(f"    ⚠️  Section {sec_key}: source missing → {src}")
            new_urls[sec_key] = sec_val if isinstance(sec_val, dict) else {'main': sec_val}
            continue

        ext = os.path.splitext(src)[1] or '.mp3'
        dst_name = f"section_{sec_key}{ext}"
        dst = os.path.join(vdir, dst_name)

        try:
            shutil.copy2(src, dst)
            rel_url = f"/static/full_test_bank/variant_{variant_id:03d}/{dst_name}"
            new_urls[sec_key] = {'main': rel_url, **rest}
            size_kb = os.path.getsize(dst) // 1024
            print(f"    ✅ Section {sec_key}: {size_kb} KB → {dst_name}")
        except Exception as e:
            print(f"    ❌ Section {sec_key}: copy failed — {e}")
            new_urls[sec_key] = sec_val if isinstance(sec_val, dict) else {'main': sec_val}

    return new_urls


def migrate_variant(variant, static_root, force=False, dry_run=False):
    """Migrate a single variant to disk."""
    vid = variant.id
    vdir = os.path.join(static_root, 'full_test_bank', f'variant_{vid:03d}')
    snapshot_path = os.path.join(vdir, 'snapshot.json')

    print(f"\n{'─' * 60}")
    print(f"📦 Variant #{vid}  (difficulty={variant.difficulty}, status={variant.status})")
    print(f"   Target folder: {vdir}")

    # Check if already migrated
    if os.path.exists(snapshot_path) and not force:
        size = os.path.getsize(snapshot_path)
        print(f"   ⏭️  snapshot.json already exists ({size} bytes) — skipping")
        print(f"   💡 Use --force to overwrite")
        return 'skipped'

    # Get snapshot from DB
    snapshot = variant.snapshot
    if not snapshot:
        print(f"   ❌ DB row has no snapshot — cannot migrate")
        return 'failed'

    if isinstance(snapshot, str):
        try:
            snapshot = json.loads(snapshot)
        except Exception as e:
            print(f"   ❌ Failed to parse snapshot JSON: {e}")
            return 'failed'

    # Show what's in the snapshot
    phases = [k for k in ('listening', 'reading', 'writing', 'speaking') if k in snapshot]
    print(f"   📋 Snapshot phases: {', '.join(phases)}")

    if dry_run:
        print(f"   🔍 DRY RUN — would create:")
        print(f"      {snapshot_path}")
        return 'dry-run'

    # ─── Create folder ─────────────────────────────────────
    try:
        os.makedirs(vdir, exist_ok=True)
    except Exception as e:
        print(f"   ❌ Cannot create folder: {e}")
        return 'failed'

    # ─── Copy audio ────────────────────────────────────────
    audio_urls = {}
    if isinstance(snapshot.get('listening'), dict):
        audio_urls = snapshot['listening'].get('audio_urls') or {}

    # Fallback: use variant.listening_audio_urls if snapshot has none
    if not audio_urls and variant.listening_audio_urls:
        audio_urls = dict(variant.listening_audio_urls)
        print(f"   ℹ️  Using variant.listening_audio_urls ({len(audio_urls)} sections)")

    if audio_urls:
        print(f"   🎧 Copying {len(audio_urls)} audio section(s)...")
        new_audio_urls = copy_audio_to_variant(vid, audio_urls, static_root)

        # Update snapshot with new URLs
        if 'listening' not in snapshot or not isinstance(snapshot['listening'], dict):
            snapshot['listening'] = {}
        snapshot['listening']['audio_urls'] = new_audio_urls
        snapshot['listening']['audio_timings'] = (
            variant.listening_audio_timings or {}
        )
    else:
        print(f"   ⚠️  No audio URLs in DB — snapshot will have no audio")
        if 'listening' in snapshot and isinstance(snapshot['listening'], dict):
            snapshot['listening'].setdefault('audio_urls', {})

    # ─── Write snapshot.json ───────────────────────────────
    try:
        with open(snapshot_path, 'w', encoding='utf-8') as f:
            json.dump(snapshot, f, indent=2, ensure_ascii=False)
        size = os.path.getsize(snapshot_path)
        print(f"   ✅ snapshot.json written ({size} bytes)")

        # Also sync back to DB (updated audio URLs)
        variant.snapshot = snapshot
        if audio_urls:
            variant.listening_audio_urls = snapshot['listening']['audio_urls']
        db.session.commit()
        print(f"   ✅ DB updated with new audio URLs")
    except Exception as e:
        print(f"   ❌ Failed to write snapshot.json: {e}")
        return 'failed'

    return 'success'


def main():
    parser = argparse.ArgumentParser(description='Migrate FullTestVariant rows to disk')
    parser.add_argument('--variant', type=int, help='Only migrate this variant ID')
    parser.add_argument('--force', action='store_true', help='Overwrite existing files')
    parser.add_argument('--dry-run', action='store_true', help='Show what would happen')
    args = parser.parse_args()

    print(f"\n{'═' * 60}")
    print(f"🚀 Full-Test Variant Migration")
    print(f"{'═' * 60}")

    with app.app_context():
        static_root = app.static_folder
        print(f"📁 Static root: {static_root}")

        bank_dir = os.path.join(static_root, 'full_test_bank')
        os.makedirs(bank_dir, exist_ok=True)
        print(f"📁 Bank dir: {bank_dir}")

        FTV = get_full_test_variant_model('ielts')

        # Query
        if args.variant:
            variants = [db.session.get(FTV, args.variant)]
            variants = [v for v in variants if v is not None]
            if not variants:
                print(f"\n❌ Variant #{args.variant} not found in DB")
                return 1
        else:
            variants = FTV.query.order_by(FTV.id.asc()).all()

        print(f"\n📊 Found {len(variants)} variant(s) in DB\n")

        results = {'success': 0, 'skipped': 0, 'failed': 0, 'dry-run': 0}

        for v in variants:
            r = migrate_variant(
                v, static_root,
                force=args.force,
                dry_run=args.dry_run,
            )
            results[r] = results.get(r, 0) + 1

        # ─── Summary ───────────────────────────────────────
        print(f"\n{'═' * 60}")
        print(f"📊 Migration Summary")
        print(f"{'═' * 60}")
        print(f"   ✅ Success : {results['success']}")
        print(f"   ⏭️  Skipped : {results['skipped']}")
        print(f"   ❌ Failed  : {results['failed']}")
        if args.dry_run:
            print(f"   🔍 Dry-run : {results['dry-run']}")
        print(f"{'═' * 60}\n")

        # Verify
        if not args.dry_run:
            print(f"📁 Contents of {bank_dir}:")
            for name in sorted(os.listdir(bank_dir)):
                full = os.path.join(bank_dir, name)
                if os.path.isdir(full):
                    files = os.listdir(full)
                    marker = "✅" if 'snapshot.json' in files else "⚠️ "
                    print(f"   {marker} {name}/  ({len(files)} files)")
                else:
                    print(f"       {name}")
            print()

        return 0 if results['failed'] == 0 else 1


if __name__ == '__main__':
    sys.exit(main())