import os
src = r'f:\【TRAE】\数脉AI\index.html'
data_dir = r'f:\【TRAE】\数脉AI\data'
with open(src, 'r', encoding='utf-8') as f:
    all_lines = f.readlines()
shishuo_lines = all_lines[2167:2254]
header = '// ' + '\u4e16' + '\u8bf4' + '\u65b0' + '\u8bed' + '\u6570' + '\u636e' + ' - ' + '\u53e4' + '\u7c4d' + '\u8bed' + '\u6599' + '\u5e93' + '\n'
header += 'window.__bookData = window.__bookData || {};\n'
header += 'window.__bookData["' + '\u4e16' + '\u8bf4' + '\u65b0' + '\u8bed' + '"] = {\n'
footer = '};\n'
with open(os.path.join(data_dir, 'shishuoxinyu.js'), 'w', encoding='utf-8') as f:
    f.write(header)
    f.writelines(shishuo_lines)
    f.write(footer)
tiangong_lines = all_lines[2254:2340]
header3 = '// ' + '\u5929' + '\u5de5' + '\u5f00' + '\u7269' + '\u6570' + '\u636e' + ' - ' + '\u53e4' + '\u7c4d' + '\u8bed' + '\u6599' + '\u5e93' + '\n'
header3 += 'window.__bookData = window.__bookData || {};\n'
header3 += 'window.__bookData["' + '\u5929' + '\u5de5' + '\u5f00' + '\u7269' + '"] = {\n'
with open(os.path.join(data_dir, 'tiangongkaiwu.js'), 'w', encoding='utf-8') as f:
    f.write(header3)
    f.writelines(tiangong_lines)
    f.write(footer)
print('shishuoxinyu.js: ' + str(3+len(shishuo_lines)+1) + ' lines')
print('tiangongkaiwu.js: ' + str(3+len(tiangong_lines)+1) + ' lines')
