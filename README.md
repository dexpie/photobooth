# Photo Booth

Aplikasi photobooth desktop berbasis Python untuk mengambil beberapa foto dan menyusunnya dalam bingkai pilihan. Hasil JPG dan GIF disimpan secara otomatis; foto juga dapat dibagikan lewat QR.

## Fitur

- Pilihan kamera, template bingkai, dan filter dengan live preview.
- Countdown untuk setiap foto, GIF boomerang, serta retake satu foto atau seluruh sesi.
- Penyimpanan otomatis JPG dan GIF di folder `riwayat/`.
- Download foto, GIF, atau keduanya dalam satu ZIP melalui QR.
- Percobaan sambung ulang saat kamera terputus dan pelepasan kamera saat idle.

## Persyaratan

- Windows dengan Python 3.11 dan Tkinter (biasanya tersedia dalam instalasi Python).
- Webcam yang terhubung dan dapat diakses aplikasi.
- Koneksi internet hanya diperlukan untuk membagikan hasil lewat QR.

## Instalasi dan menjalankan

Di PowerShell, jalankan:

```powershell
git clone https://github.com/dexpie/photobooth.git
cd photobooth
python -m venv .venv
.\.venv\Scripts\python -m pip install -r requirements.txt
.\.venv\Scripts\python app.py
```

Alternatifnya, aktifkan virtual environment lalu jalankan `run_app.bat`. Jalankan aplikasi dari folder utama proyek agar template dapat ditemukan.

## Penggunaan

1. Pilih kamera dan filter, lalu pilih template.
2. Tekan **Spasi** pada jendela kamera untuk memulai countdown. Jumlah foto mengikuti template; setiap foto memiliki countdown enam detik.
3. Hasil tersimpan otomatis di `riwayat/` dan ditampilkan di preview. Gunakan tombol retake untuk mengganti satu foto atau mengulang sesi.
4. Untuk membagikan hasil, pilih tombol download di preview lalu scan QR. Tombol gabungan mengemas foto dan GIF dalam satu ZIP.

Di jendela preview, **Tab** dan **Shift+Tab** berpindah antar tombol, **Enter** atau **Spasi** menjalankan pilihan, dan **Esc** menutup preview. Kamera dijeda setelah 60 detik tanpa aktivitas di halaman template; gerakkan mouse atau tekan tombol untuk mengaktifkannya kembali.

### Privasi dan berbagi

Hasil foto disimpan secara lokal di `riwayat/` dan tidak disertakan dalam Git. Tombol download mengunggah file yang dipilih ke layanan pihak ketiga [tmpfiles.org](https://tmpfiles.org/); siapa pun yang memperoleh tautan dapat mengakses file tersebut. Jangan unggah foto tanpa persetujuan orang yang ada di dalamnya. Ketersediaan dan masa berlaku tautan bergantung pada layanan tersebut.

## Menambahkan template

1. Buat folder baru di `assets/templates/`, misalnya `assets/templates/template_baru/`.
2. Masukkan bingkai PNG bernama `overlay.png`, dengan area foto transparan.
3. Dari folder utama proyek, jalankan `python import_templates.py` atau `run_import.bat`.
4. Periksa hasilnya dan mulai ulang aplikasi. Script membuat `config.json` dan `thumbnail.png` berdasarkan area foto yang terdeteksi.

**Perhatian:** script import memindai semua folder template dan dapat menulis ulang `config.json` serta mengubah `overlay.png` yang sudah ada. Cadangkan perubahan template sebelum menjalankannya.

`config.json` menentukan ukuran kanvas, jumlah foto, dan posisi slot `[x, y, lebar, tinggi]`. Ukuran maksimal GIF dapat diatur dengan `"gif_size": [lebar, tinggi]`; default-nya 480 × 720 piksel.

## Pengujian

```powershell
python -m unittest discover -s tests
```

Beberapa pengujian antarmuka memerlukan sesi desktop dengan Tkinter. Untuk memeriksa kamera dan alur berbagi, jalankan aplikasi secara langsung.

## Kontribusi

Laporan bug dan usulan perubahan dapat dikirim melalui [Issues](https://github.com/dexpie/photobooth/issues). Untuk perubahan kode, buat fork, kerjakan di branch terpisah, jalankan pengujian, lalu buka pull request dengan penjelasan perubahan.

## Lisensi

Proyek ini menggunakan [MIT License](LICENSE).
