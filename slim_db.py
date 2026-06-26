"""
精简数据库 (直接修改 stock_data.db)
- 删除无用大表 (stock_features, stock_cyq, stock_float_mv, stock_concept)
- 保留 index_daily (batch_screen.py 需要沪深300数据)
- 裁剪 stock_daily 只保留最近90天
- VACUUM 回收空间
"""
import sqlite3, os, sys

# Windows 终端兼容：强制 UTF-8
if sys.platform == 'win32':
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'stock_data.db')


def main():
    before_mb = os.path.getsize(DB_PATH) / (1024 * 1024)
    conn = sqlite3.connect(DB_PATH)

    # 1. 删除不需要的大表
    print('[slim] Dropping unused tables...')
    for tbl in ['stock_features', 'stock_cyq', 'stock_float_mv', 'stock_concept']:
        try:
            conn.execute(f"DROP TABLE IF EXISTS {tbl}")
            print(f'  [OK] DROP {tbl}')
        except Exception as e:
            print(f'  [SKIP] {tbl}: {e}')

    # 2. 裁剪 stock_daily — 只保留最近90天
    print('[slim] Pruning stock_daily to 90 days...')
    conn.execute("""
        DELETE FROM stock_daily
        WHERE date < (SELECT DATE(MAX(date), '-90 days') FROM stock_daily)
    """)
    deleted = conn.total_changes
    print(f'  [OK] Deleted {deleted} old rows')

    # 3. 清理 screening_history 中的无效日期 (<=20条记录)
    print('[slim] Cleaning sparse screening_history dates...')
    cursor = conn.execute("""
        DELETE FROM screening_history
        WHERE target_date IN (
            SELECT target_date FROM screening_history
            GROUP BY target_date HAVING COUNT(*) <= 20
        )
    """)
    cleaned = cursor.rowcount
    print(f'  [OK] Cleaned {cleaned} sparse records')

    # 4. 删除 sqlite_sequence (如果有)
    try:
        conn.execute("DROP TABLE IF EXISTS sqlite_sequence")
    except Exception:
        pass

    conn.commit()

    # 5. VACUUM
    print('[slim] VACUUM (reclaiming disk space)...')
    conn.execute("VACUUM")
    conn.close()

    after_mb = os.path.getsize(DB_PATH) / (1024 * 1024)
    print(f'\n[slim] Done: {before_mb:.0f} MB -> {after_mb:.0f} MB (saved {before_mb - after_mb:.0f} MB)')

    # 报告保留的数据
    conn = sqlite3.connect(DB_PATH)
    for table in ['stock_basic', 'stock_daily', 'index_daily', 'screening_history']:
        try:
            cnt = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            print(f'  {table}: {cnt} rows')
        except Exception:
            print(f'  {table}: (not found)')
    conn.close()


if __name__ == '__main__':
    main()
