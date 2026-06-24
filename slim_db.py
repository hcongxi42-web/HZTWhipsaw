"""
生成精简数据库 (用于 GitHub Actions 工作流)
只保留最近 90 个交易日的 stock_daily + 全部 screening_history
"""
import sqlite3, os

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'stock_data.db')
SLIM_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'stock_data_slim.db')

def main():
    if os.path.exists(SLIM_PATH):
        os.remove(SLIM_PATH)

    src = sqlite3.connect(DB_PATH)
    dst = sqlite3.connect(SLIM_PATH)

    # 1. 只复制需要的表 (stock_basic, stock_daily, screening_history)
    src.backup(dst)

    # 2. 删除不需要的大表
    print('Dropping non-essential tables...')
    for tbl in ['stock_features', 'stock_cyq', 'stock_float_mv', 'stock_concept',
                'index_daily', 'sqlite_sequence']:
        try:
            dst.execute(f"DROP TABLE IF EXISTS {tbl}")
            print(f'  DROP {tbl}')
        except:
            pass

    # 3. 删除 stock_daily 中 90 天以前的数据
    print('Pruning old stock_daily data...')
    dst.execute("""
        DELETE FROM stock_daily
        WHERE date < (SELECT DATE(MAX(date), '-90 days') FROM stock_daily)
    """)

    dst.commit()

    # 3. VACUUM to reclaim space
    print('Vacuuming...')
    dst.execute("VACUUM")

    # 4. Report
    for table in ['stock_basic', 'stock_daily', 'screening_history']:
        cnt = dst.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        print(f'  {table}: {cnt} rows')

    dst.close()
    src.close()

    size_mb = os.path.getsize(SLIM_PATH) / (1024 * 1024)
    print(f'\n精简数据库: {SLIM_PATH}')
    print(f'大小: {size_mb:.1f} MB')

if __name__ == '__main__':
    main()
