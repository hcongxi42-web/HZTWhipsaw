"""
精简数据库 (直接修改 stock_data.db)
- 删除无用大表 (stock_features, stock_cyq, stock_float_mv, stock_concept)
- 保留 index_daily (batch_screen.py 需要沪深300数据)
- 裁剪 stock_daily 只保留最近90天
- VACUUM 回收空间
"""
import sqlite3, os

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'stock_data.db')


def main():
    before_mb = os.path.getsize(DB_PATH) / (1024 * 1024)
    conn = sqlite3.connect(DB_PATH)

    # 1. 删除不需要的大表
    print('删除非必要表...')
    for tbl in ['stock_features', 'stock_cyq', 'stock_float_mv', 'stock_concept']:
        try:
            conn.execute(f"DROP TABLE IF EXISTS {tbl}")
            print(f'  ✓ DROP {tbl}')
        except Exception:
            pass

    # 2. 裁剪 stock_daily — 只保留最近90天
    print('裁剪 stock_daily...')
    conn.execute("""
        DELETE FROM stock_daily
        WHERE date < (SELECT DATE(MAX(date), '-90 days') FROM stock_daily)
    """)
    deleted = conn.total_changes
    print(f'  ✓ 删除了 {deleted} 行旧数据')

    # 3. 清理 screening_history 中的无效日期 (≤20条记录)
    print('清理 screening_history 稀疏日期...')
    cursor = conn.execute("""
        DELETE FROM screening_history
        WHERE target_date IN (
            SELECT target_date FROM screening_history
            GROUP BY target_date HAVING COUNT(*) <= 20
        )
    """)
    cleaned = cursor.rowcount
    print(f'  ✓ 清理了 {cleaned} 条稀疏日期记录')

    # 4. 删除 sqlite_sequence (如果有)
    try:
        conn.execute("DROP TABLE IF EXISTS sqlite_sequence")
    except Exception:
        pass

    conn.commit()

    # 5. VACUUM
    print('VACUUM 回收空间...')
    conn.execute("VACUUM")
    conn.close()

    after_mb = os.path.getsize(DB_PATH) / (1024 * 1024)
    print(f'\n数据库精简完成: {before_mb:.0f} MB → {after_mb:.0f} MB (减少 {before_mb - after_mb:.0f} MB)')

    # 报告保留的数据
    conn = sqlite3.connect(DB_PATH)
    for table in ['stock_basic', 'stock_daily', 'index_daily', 'screening_history']:
        try:
            cnt = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            print(f'  {table}: {cnt} 行')
        except Exception:
            print(f'  {table}: (不存在)')
    conn.close()


if __name__ == '__main__':
    main()
