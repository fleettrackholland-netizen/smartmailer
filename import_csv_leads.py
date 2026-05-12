#!/usr/bin/env python3
"""
CSV Lead Import Script — Tüm CSV lead'leri veritabanına aktar.
Duplicate email'ler otomatik birleştirilir (upsert).
Mevcut veritabanı lead'leri korunur.

LOCKED: 2026-05-12 — csv_import source %77.2 MX-invalid, 77.050 lead quarantine'de.
Yeni toplu CSV import YASAK. Sadece MX-validated, manuel onaylı listeler girebilir.
Çalıştırmak için bu bloğu kaldır ve sebebini commit mesajına yaz.
"""
import sys
# ── HARD LOCK ──────────────────────────────────────────────────────────────
_LOCK_MSG = (
    "DURDURULDU: import_csv_leads.py kilitli (2026-05-12).\n"
    "csv_import source 77.050 lead quarantine'de (%77.2 MX-invalid).\n"
    "Yeni CSV import için önce MX audit yapın, sonra kilidi kaldırın."
)
print(_LOCK_MSG, file=sys.stderr)
sys.exit(1)
# ── LOCK SONU ──────────────────────────────────────────────────────────────
import os
import sys
import csv
import sqlite3
import glob
from datetime import datetime

# Proje dizini
PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, PROJECT_ROOT)

DB_PATH = os.path.join(PROJECT_ROOT, "data", "smartmailer_ultimate.db")
CSV_DIR = os.path.join(PROJECT_ROOT, "mail adresleri")

def get_db_count(db_path):
    """Veritabanındaki lead sayısını döndür."""
    conn = sqlite3.connect(db_path)
    count = conn.execute("SELECT COUNT(*) FROM leads").fetchone()[0]
    conn.close()
    return count

def import_csv_to_db(csv_path, db_path):
    """CSV dosyasını veritabanına aktar. Upsert mantığı ile duplicate önlenir."""
    conn = sqlite3.connect(db_path, timeout=30)
    conn.execute("PRAGMA journal_mode=WAL")
    
    imported = 0
    skipped = 0
    errors = 0
    
    try:
        with open(csv_path, encoding="utf-8-sig", newline="") as f:
            # Delimiter tespiti (semicolon vs comma)
            sample = f.read(2048)
            f.seek(0)
            dialect = csv.Sniffer().sniff(sample, delimiters=',;')
            reader = csv.DictReader(f, dialect=dialect)
            
            if not reader.fieldnames:
                print(f"  ⚠️ Boş CSV: {csv_path}")
                return 0, 0, 0
            
            # CSV sütun isimlerini normalize et
            fieldnames = [fn.strip() for fn in reader.fieldnames]
            # Eğer tek bir sütun geldiyse (semicolon ile ayrılmış ama virgül aranmışsa)
            if len(fieldnames) == 1 and ';' in fieldnames[0]:
                f.seek(0)
                reader = csv.DictReader(f, delimiter=';')
                fieldnames = [fn.strip() for fn in reader.fieldnames]
            
            print(f"  📋 Sütunlar: {fieldnames}")
            
            for row in reader:
                try:
                    # Email al (farklı sütun isimleri destekle)
                    email = (row.get("Email") or row.get("email") or 
                             row.get("EMAIL") or row.get("e-mail") or "").strip().lower()
                    
                    if not email or "@" not in email:
                        skipped += 1
                        continue
                    
                    company = (row.get("Company") or row.get("company") or 
                              row.get("Bedrijf") or row.get("bedrijf") or "").strip()
                    sector = (row.get("Sector") or row.get("sector") or 
                             row.get("Branche") or row.get("branche") or "").strip()
                    location = (row.get("Location") or row.get("location") or 
                               row.get("Locatie") or row.get("locatie") or 
                               row.get("Stad") or row.get("stad") or "").strip()
                    website = (row.get("Website") or row.get("website") or 
                              row.get("URL") or row.get("url") or "").strip()
                    phone = (row.get("Phone") or row.get("phone") or 
                            row.get("Telefoon") or row.get("telefoon") or "").strip()
                    
                    vehicles = 0
                    veh_str = (row.get("Vehicles") or row.get("vehicles") or 
                              row.get("Voertuigen") or row.get("voertuigen") or "0")
                    try:
                        vehicles = int(str(veh_str).strip())
                    except (ValueError, TypeError):
                        vehicles = 0
                    
                    # Upsert — INSERT OR UPDATE
                    conn.execute("""
                        INSERT INTO leads (email, company, sector, location, vehicles, phone, website, source, status)
                        VALUES (?, ?, ?, ?, ?, ?, ?, 'csv_import', 'new')
                        ON CONFLICT(email) DO UPDATE SET
                            company = CASE WHEN excluded.company != '' AND (leads.company = '' OR leads.company IS NULL) 
                                          THEN excluded.company ELSE leads.company END,
                            sector = CASE WHEN excluded.sector != '' AND (leads.sector = '' OR leads.sector IS NULL) 
                                         THEN excluded.sector ELSE leads.sector END,
                            location = CASE WHEN excluded.location != '' AND (leads.location = '' OR leads.location IS NULL) 
                                           THEN excluded.location ELSE leads.location END,
                            vehicles = CASE WHEN excluded.vehicles > 0 AND leads.vehicles = 0 
                                           THEN excluded.vehicles ELSE leads.vehicles END,
                            phone = CASE WHEN excluded.phone != '' AND (leads.phone = '' OR leads.phone IS NULL) 
                                        THEN excluded.phone ELSE leads.phone END,
                            website = CASE WHEN excluded.website != '' AND (leads.website = '' OR leads.website IS NULL) 
                                          THEN excluded.website ELSE leads.website END,
                            updated_at = datetime('now')
                    """, (email, company, sector, location, vehicles, phone, website))
                    
                    imported += 1
                    
                except Exception as e:
                    errors += 1
                    if errors <= 5:
                        print(f"  ⚠️ Satır hatası: {e}")
        
        conn.commit()
    finally:
        conn.close()
    
    return imported, skipped, errors

def main():
    print("=" * 60)
    print("  SmartMailer CSV Lead Import")
    print("  Tüm CSV lead'leri veritabanına aktarılacak")
    print("=" * 60)
    
    # Veritabanı mevcut mu?
    if not os.path.exists(DB_PATH):
        print(f"❌ Veritabanı bulunamadı: {DB_PATH}")
        sys.exit(1)
    
    before_count = get_db_count(DB_PATH)
    print(f"\n📊 Mevcut lead sayısı: {before_count}")
    
    # CSV dosyalarını bul
    csv_files = []
    
    # 1. 'mail adresleri' klasörü
    if os.path.isdir(CSV_DIR):
        csv_files.extend(glob.glob(os.path.join(CSV_DIR, "*.csv")))
    
    # 2. exports klasörü
    exports_dir = os.path.join(PROJECT_ROOT, "exports")
    if os.path.isdir(exports_dir):
        csv_files.extend(glob.glob(os.path.join(exports_dir, "*.csv")))
    
    # 3. data klasörü
    data_dir = os.path.join(PROJECT_ROOT, "data")
    if os.path.isdir(data_dir):
        csv_files.extend(glob.glob(os.path.join(data_dir, "leads*.csv")))
    
    if not csv_files:
        print("❌ CSV dosyası bulunamadı!")
        sys.exit(1)
    
    print(f"\n📁 {len(csv_files)} CSV dosyası bulundu:")
    
    total_imported = 0
    total_skipped = 0
    total_errors = 0
    
    # leads_ALLES.csv varsa önce onu aktar (en kapsamlı)
    alles_files = [f for f in csv_files if "ALLES" in f.upper()]
    other_files = [f for f in csv_files if "ALLES" not in f.upper()]
    ordered_files = alles_files + other_files
    
    for csv_path in ordered_files:
        fname = os.path.basename(csv_path)
        fsize = os.path.getsize(csv_path) / 1024  # KB
        print(f"\n  📄 {fname} ({fsize:.0f} KB)")
        
        imported, skipped, errors = import_csv_to_db(csv_path, DB_PATH)
        total_imported += imported
        total_skipped += skipped
        total_errors += errors
        
        print(f"     ✅ {imported} aktarıldı | ⏭️ {skipped} atlandı | ❌ {errors} hata")
    
    after_count = get_db_count(DB_PATH)
    new_leads = after_count - before_count
    
    print(f"\n{'=' * 60}")
    print(f"  📊 SONUÇ")
    print(f"  Önceki: {before_count}")
    print(f"  Sonraki: {after_count}")
    print(f"  Yeni eklenen: {new_leads}")
    print(f"  Toplam işlenen: {total_imported}")
    print(f"  Atlanan: {total_skipped}")
    print(f"  Hata: {total_errors}")
    print(f"{'=' * 60}")

if __name__ == "__main__":
    main()
