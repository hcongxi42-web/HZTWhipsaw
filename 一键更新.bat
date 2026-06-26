@echo off
cd /d "C:\Users\32299\Desktop\新建文件夹"
echo ============================================
echo  [1/4] slim_db - 精简数据库
echo ============================================
python slim_db.py
if %errorlevel% neq 0 (
    echo [FAIL] slim_db failed, stopping
    pause
    exit /b 1
)

echo.
echo ============================================
echo  [2/4] generate_static - 生成静态站点
echo ============================================
python generate_static.py

echo.
echo ============================================
echo  [3/4] delete old slim copy
echo ============================================
if exist stock_data_slim.db (
    del stock_data_slim.db
    echo [OK] Deleted stock_data_slim.db
)

echo.
echo ============================================
echo  [4/4] commit and push
echo ============================================
git add -A
git commit -m "Full update: slim DB + regenerate static site"
git pull --rebase origin main
git push

echo.
echo ============================================
echo  DONE! All steps completed.
echo ============================================
pause
