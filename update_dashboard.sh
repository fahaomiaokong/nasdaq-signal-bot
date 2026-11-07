#!/bin/bash
# 一键更新仪表盘数据并重新生成 HTML
cd "$(dirname "$0")"

echo "=== 更新数据 ==="
/Users/mengxiang/.workbuddy/binaries/python/envs/default/bin/python generate_dashboard.py

echo ""
echo "=== 生成 HTML ==="
/Users/mengxiang/.workbuddy/binaries/python/envs/default/bin/python -c "
import json
with open('dashboard_data.json') as f:
    data = json.dumps(json.load(f), ensure_ascii=False)
with open('dashboard_template.html') as f:
    template = f.read()
html = template.replace('%EMBED_DATA%', data)
with open('dashboard.html', 'w') as f:
    f.write(html)
print(f'dashboard.html: {len(html)} chars')
"

echo ""
echo "=== 完成 ==="
echo "打开仪表盘: open dashboard.html"
