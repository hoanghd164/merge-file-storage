## 📦 Thông tin về Database
### DB sẽ có **hai bảng**:

* `filemeta_local` — cache cho **local (server1)**
* `filemeta_remote` — cache cho **remote (server2)**

### Xem danh sách bảng & schema

```bash
shell> sqlite3 /var/tmp/merge_hash_cache.sqlite ".tables"
filemeta_local   filemeta_remote

shell> sqlite3 /var/tmp/merge_hash_cache.sqlite ".schema filemeta_local"
CREATE TABLE filemeta_local(
            root TEXT, rel TEXT, size INTEGER, mtime INTEGER,
            algo TEXT, hash TEXT, last_seen INTEGER, last_hashed INTEGER,
            PRIMARY KEY(root, rel, algo)
        );
CREATE INDEX idx_local_algo_hash ON filemeta_local(algo, hash);
CREATE INDEX idx_local_last_seen ON filemeta_local(last_seen);

shell> sqlite3 /var/tmp/merge_hash_cache.sqlite ".schema filemeta_remote"
CREATE TABLE filemeta_remote(
            host TEXT, root TEXT, rel TEXT, size INTEGER, mtime INTEGER,
            algo TEXT, hash TEXT, last_seen INTEGER, last_hashed INTEGER,
            PRIMARY KEY(host, root, rel, algo)
        );
CREATE INDEX idx_remote_algo_hash ON filemeta_remote(algo, hash);
CREATE INDEX idx_remote_last_seen ON filemeta_remote(last_seen);
```

### Xem vài bản ghi đầu

```bash
shell> sqlite3 -header -column /var/tmp/merge_hash_cache.sqlite \
"SELECT * FROM filemeta_local LIMIT 10;"
root        rel                                      size        mtime       algo    hash                                                              last_seen   last_hashed
----------  ---------------------------------------  ----------  ----------  ------  ----------------------------------------------------------------  ----------  -----------
/home/data  manjaro-xfce-21.3.6-220729-linux515.iso  3529039872  1754793215  sha256  a2be725e95f8ca6f1f70078ee210e5cf7bae483f638f1e97bbc944625cbe67c4  1754811351  1754795573 
/home/data  ceph_pg_object_summary.sh                2876        1754761760  sha256  ab57be9b0eee09a025c424eebe48819048d52dd6b40bab072b10c9270c5985d5  1754833978  1754795741 
/home/data  ceph.drawio                              129923      1754761760  sha256  a7268fdb1f79026d2f63cb6782ca4c09d05e6a1073b8f7eab4013e90dbc7fcb5  1754833978  1754795741 
/home/data  osd-reweight-down.sh                     4369        1754761761  sha256  6a3ddb949a682df7730e71aac061adbe57746465122e18d0e3c3a07966105950  1754833978  1754795741 
/home/data  ssacli.old.sh                            4277        1754761761  sha256  49d91c3069bab6227e1a3fb94b727d69a623f2ccaf76bcdec4e86e099d587684  1754833978  1754795741 
/home/data  ceph_osd_summary.py                      2395        1754761760  sha256  27be4c3c6c5bb86ddde4597fb21ed62be4cd926cd4ae8b821fca50917f05839a  1754833978  1754795741 
/home/data  lldpNeighbors.py                         8382        1754761761  sha256  4bfeef7d1263129951a3a852a1a1da555b305b1b5e34f9ce3aae60cd74518c1f  1754833978  1754795741 
/home/data  audit_vstor-hcm04_20250718_150347.log    7817        1754761760  sha256  7e8b735a0c3e4aa3216a23017eaf1e25789f7a3c7e5a8c6c6b3a6ebb6b7ef223  1754833978  1754795741 
/home/data  ubuntu-18_04_6-live-server-amd64.iso     1016070144  1754792953  sha256  6c647b1ab4318e8c560d5748f908e108be654bad1e165f7cf4f3c1fc43995934  1754811351  1754795741 
/home/data  2024_02_11_19_19_IMG_4346_1_Edit.mp4     164515010   1754766600  sha256  c67ee73b781138ff7cf916bd48d3bfa6254d6e2f2208aec03ec694dd678d5b9e  1754833978  1754795741

shell> sqlite3 -header -column /var/tmp/merge_hash_cache.sqlite \
"SELECT * FROM filemeta_remote LIMIT 10;"
host         root        rel                                    size        mtime       algo    hash                                                              last_seen   last_hashed
-----------  ----------  -------------------------------------  ----------  ----------  ------  ----------------------------------------------------------------  ----------  -----------
10.237.7.75  /home/data  bench.sh                               7090        1754827993  sha256  3034d3017da090db0d1ba4729372d1d7c5e47d7afdd5dbf29083825d7f134824  1754834009  1754828015 
10.237.7.75  /home/data  ceph_pg_object_summary.sh              2876        1754761760  sha256  ab57be9b0eee09a025c424eebe48819048d52dd6b40bab072b10c9270c5985d5  1754834009  1754795582 
10.237.7.75  /home/data  ceph.drawio                            129923      1754761760  sha256  a7268fdb1f79026d2f63cb6782ca4c09d05e6a1073b8f7eab4013e90dbc7fcb5  1754834009  1754795582 
10.237.7.75  /home/data  osd-reweight-down.sh                   4369        1754761761  sha256  6a3ddb949a682df7730e71aac061adbe57746465122e18d0e3c3a07966105950  1754834009  1754795582 
10.237.7.75  /home/data  ssacli.old.sh                          4277        1754761761  sha256  49d91c3069bab6227e1a3fb94b727d69a623f2ccaf76bcdec4e86e099d587684  1754834009  1754795582 
10.237.7.75  /home/data  ceph_osd_summary.py                    2395        1754761760  sha256  27be4c3c6c5bb86ddde4597fb21ed62be4cd926cd4ae8b821fca50917f05839a  1754834009  1754795582 
10.237.7.75  /home/data  lldpNeighbors.py                       8382        1754761761  sha256  4bfeef7d1263129951a3a852a1a1da555b305b1b5e34f9ce3aae60cd74518c1f  1754834009  1754795582 
10.237.7.75  /home/data  audit_vstor-hcm04_20250718_150347.log  7817        1754761760  sha256  7e8b735a0c3e4aa3216a23017eaf1e25789f7a3c7e5a8c6c6b3a6ebb6b7ef223  1754834009  1754795582 
10.237.7.75  /home/data  ubuntu-18_04_6-live-server-amd64.iso   1016070144  1754792953  sha256  6c647b1ab4318e8c560d5748f908e108be654bad1e165f7cf4f3c1fc43995934  1754811351  1754795582 
10.237.7.75  /home/data  2024_02_11_19_19_IMG_4346_1_Edit.mp4   164515010   1754766600  sha256  c67ee73b781138ff7cf916bd48d3bfa6254d6e2f2208aec03ec694dd678d5b9e  1754834009  1754795582
```

### Một số truy vấn khác

* Local: các file đã hash

```bash
shell> sqlite3 -header -column /var/tmp/merge_hash_cache.sqlite \
"SELECT root,rel,size,mtime,algo,substr(hash,1,12) AS hash12,last_hashed FROM filemeta_local WHERE hash IS NOT NULL LIMIT 20;"
root        rel                                      size        mtime       algo    hash12        last_hashed
----------  ---------------------------------------  ----------  ----------  ------  ------------  -----------
/home/data  manjaro-xfce-21.3.6-220729-linux515.iso  3529039872  1754793215  sha256  a2be725e95f8  1754795573 
/home/data  ceph_pg_object_summary.sh                2876        1754761760  sha256  ab57be9b0eee  1754795741 
/home/data  ceph.drawio                              129923      1754761760  sha256  a7268fdb1f79  1754795741 
/home/data  osd-reweight-down.sh                     4369        1754761761  sha256  6a3ddb949a68  1754795741 
/home/data  ssacli.old.sh                            4277        1754761761  sha256  49d91c3069ba  1754795741 
/home/data  ceph_osd_summary.py                      2395        1754761760  sha256  27be4c3c6c5b  1754795741 
/home/data  lldpNeighbors.py                         8382        1754761761  sha256  4bfeef7d1263  1754795741 
/home/data  audit_vstor-hcm04_20250718_150347.log    7817        1754761760  sha256  7e8b735a0c3e  1754795741 
/home/data  ubuntu-18_04_6-live-server-amd64.iso     1016070144  1754792953  sha256  6c647b1ab431  1754795741 
/home/data  2024_02_11_19_19_IMG_4346_1_Edit.mp4     164515010   1754766600  sha256  c67ee73b7811  1754795741 
/home/data  ceph_bench_pool.sh                       1587        1754761760  sha256  b7c88153aeac  1754795741 
/home/data  vz-iso-7.5.3-391.iso                     2376044544  1754769506  sha256  97f07a1d12ed  1754795741 
/home/data  2024_02_25_19_39_IMG_5006.MOV            24531402    1754766521  sha256  1050682aa205  1754795741 
/home/data  wg_site_setup.sh                         4825        1754766418  sha256  479feb395129  1754795741 
/home/data  2024_03_02_07_34_IMG_5265.MOV            6135644     1754766524  sha256  e8349771649e  1754795741 
/home/data  getSystemInfo.py                         7224        1754761761  sha256  6cb40c51ddc3  1754795741 
/home/data  cephadm_ssh_checker.py                   4422        1754761760  sha256  49153a816878  1754795741 
/home/data  minio_benchmark.py                       4880        1754761761  sha256  d7191306b55f  1754795741 
/home/data  audit.py                                 41252       1754762077  sha256  524bd8f65071  1754795741 
/home/data  chrome32_49.0.2623.75.exe                43098374    1754791618  sha256  9e6f3020d25e  1754795741 
```

* Remote: các file **chưa** được hash (sẽ được hash dần theo budget)

```bash
shell> sqlite3 -header -column /var/tmp/merge_hash_cache.sqlite \
"SELECT host,root,rel,size,mtime FROM filemeta_remote WHERE hash IS NULL LIMIT 20;"
```

* Đếm tổng số file theo phía:

```bash
shell> sqlite3 /var/tmp/merge_hash_cache.sqlite \
"SELECT 'local' side, COUNT(*) FROM filemeta_local
 UNION ALL
 SELECT 'remote', COUNT(*) FROM filemeta_remote;"
local|41
remote|40
```

* Những file remote đã có hash (dùng để lập kế hoạch merge):

```bash
shell> sqlite3 -header -column /var/tmp/merge_hash_cache.sqlite \
"SELECT host,root,rel,size,substr(hash,1,12) hash12,last_hashed
 FROM filemeta_remote WHERE hash IS NOT NULL LIMIT 20;"
host         root        rel                                    size        hash12        last_hashed
-----------  ----------  -------------------------------------  ----------  ------------  -----------
10.237.7.75  /home/data  bench.sh                               7090        3034d3017da0  1754828015 
10.237.7.75  /home/data  ceph_pg_object_summary.sh              2876        ab57be9b0eee  1754795582 
10.237.7.75  /home/data  ceph.drawio                            129923      a7268fdb1f79  1754795582 
10.237.7.75  /home/data  osd-reweight-down.sh                   4369        6a3ddb949a68  1754795582 
10.237.7.75  /home/data  ssacli.old.sh                          4277        49d91c3069ba  1754795582 
10.237.7.75  /home/data  ceph_osd_summary.py                    2395        27be4c3c6c5b  1754795582 
10.237.7.75  /home/data  lldpNeighbors.py                       8382        4bfeef7d1263  1754795582 
10.237.7.75  /home/data  audit_vstor-hcm04_20250718_150347.log  7817        7e8b735a0c3e  1754795582 
10.237.7.75  /home/data  ubuntu-18_04_6-live-server-amd64.iso   1016070144  6c647b1ab431  1754795582 
10.237.7.75  /home/data  2024_02_11_19_19_IMG_4346_1_Edit.mp4   164515010   c67ee73b7811  1754795582 
10.237.7.75  /home/data  ceph_bench_pool.sh                     1587        b7c88153aeac  1754795582 
10.237.7.75  /home/data  vz-iso-7.5.3-391.iso                   2376044544  97f07a1d12ed  1754795582 
10.237.7.75  /home/data  2024_02_25_19_39_IMG_5006.MOV          24531402    1050682aa205  1754795582 
10.237.7.75  /home/data  wg_site_setup.sh                       4825        479feb395129  1754795582 
10.237.7.75  /home/data  nginx/ssl_checker.sh                   1821        371611c3fda0  1754795582 
10.237.7.75  /home/data  nginx/zabbix.systemd.sh                2043        8b98f2e12715  1754795582 
10.237.7.75  /home/data  2024_03_02_07_34_IMG_5265.MOV          6135644     e8349771649e  1754795582 
10.237.7.75  /home/data  getSystemInfo.py                       7224        6cb40c51ddc3  1754795582 
10.237.7.75  /home/data  cephadm_ssh_checker.py                 4422        49153a816878  1754795582 
10.237.7.75  /home/data  minio_benchmark.py                     4880        d7191306b55f  1754795582 
```

---

### Nếu hai bảng vẫn trống

Check nhanh vài điểm:

1. **Đường dẫn DB đúng chưa?** (mặc định `/var/tmp/merge_hash_cache.sqlite`)
   Bạn có sửa `CACHE_DB` trong code không?

2. **Quyền ghi**: process có ghi được vào `/var/tmp` không? (Bạn chạy `root` thì OK.)

3. **Code đã chạy `db_init()` chưa?**
   Trong file mình gửi, `main()` gọi `db_init()` ngay đầu; nếu bạn chỉnh tay, đảm bảo vẫn gọi.

4. **Log vòng lặp**:

   * Có dòng `🏠 Local hashed/updated: N file` chưa? Nếu có, `filemeta_local` phải có dữ liệu.
   * Có dòng `[remote-meta] /path: N files` chưa? Nếu có, `filemeta_remote` phải có **ít nhất** metadata.
   * Có dòng `🌐 Remote hashed this round: N file` chưa? Nếu có, `filemeta_remote.hash` sẽ dần có dữ liệu.