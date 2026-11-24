   @echo off
   cd /d %~dp0
   docker compose up -d
   :espera
   curl -s -o nul http://localhost:8000/api/config || (timeout /t 2 >nul & goto espera)
   start http://localhost:8000