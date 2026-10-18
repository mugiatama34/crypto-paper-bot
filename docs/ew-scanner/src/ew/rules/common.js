// Tüm kalıplarda geçerli yapısal kurallar ve Hasanwavebot derslerinden çıkan hatırlatmalar.
import { pass, fail, warn, info, pct, times, price } from '../engine/helpers.js';

const ALL = ['fp', 'neely', 'course'];
const HB = 'Hasanwavebot kök neden analizi (2026-09)';

export const commonRules = [
  {
    id: 'STR-1', type: 'rule', patterns: ['*'], sets: ALL, need: 3,
    name: 'Dönüş noktaları sırayla yön değiştirmeli',
    description: 'Her nokta bir dönüş (tepe veya dip) olmalıdır: dalgalar sırayla yukarı ve aşağı gider. İki ardışık dalga aynı yöne gidiyorsa aradaki nokta dönüş noktası değildir.',
    source: 'Elliott dalga tanımı (Nature\'s Law Ch.4; EWP Ders 1)',
    check(c) {
      for (let i = 1; i < c.n; i++) {
        const dir = Math.sign(c.v[i] - c.v[i - 1]);
        const expect = i % 2 === 1 ? c.d : -c.d;
        if (dir === 0) return fail(`${i - 1}. ve ${i}. noktalar aynı fiyatta.`);
        if (dir !== expect) return fail(`${i}. dalga beklenen yönün tersine gidiyor (${i - 1}. nokta bir dönüş noktası değil).`);
      }
      return pass('Noktalar sırayla tepe ve dip oluşturuyor.');
    },
  },
  {
    id: 'NEE-TIME', type: 'guideline', patterns: ['*'], sets: ['neely'], need: 4,
    name: 'Time Rule: aynı derecede ardışık üç dalga eşit süreli olamaz',
    description: "Neely'ye göre aynı derecedeki ardışık üç dalganın üçü birden aynı süreyi alamaz; en az biri belirgin şekilde farklı olmalıdır.",
    source: 'Neely, Basic Neely Extensions 9-4/9-5',
    check(c) {
      for (let i = 3; i < c.n; i++) {
        const ds = [c.D(i - 2), c.D(i - 1), c.D(i)];
        const mean = (ds[0] + ds[1] + ds[2]) / 3;
        if (mean > 0 && ds.every((x) => Math.abs(x - mean) / mean <= 0.1)) {
          return warn(`${i - 2}., ${i - 1}. ve ${i}. dalgalar neredeyse eşit sürede (±%10). Neely Time Rule'a aykırı.`);
        }
      }
      return pass('Ardışık üç dalganın süreleri birbirinden farklı.');
    },
  },

  // ---------- Hasanwavebot dersleri ----------
  {
    id: 'HB-1', type: 'reminder', patterns: ['*'], sets: ALL, need: 3,
    name: 'Az noktayla sayım güvenilmez',
    description: "Hasanwavebot yalnızca iki fiyat noktasına (p0→p1) bakıp aralarında gerçek bir itki yapısı olup olmadığını doğrulamıyordu. Kalıbın tüm dönüş noktaları girilmeden verilen sayım bir hipotezdir.",
    source: HB,
    check(c) {
      const max = c.variant.maxPoints || c.n;
      if (c.n >= max) return null;
      return info(`${c.n}/${max} nokta girildi: bu, tamamlanmış bir kalıp değil, bir hipotez. Hasanwavebot dersi: sadece 2-3 noktaya dayanan sayım gerçek yapıyı doğrulamaz.`);
    },
  },
  {
    id: 'HB-2', type: 'reminder', patterns: ['*'], sets: ALL, need: 3,
    name: "İlk dalganın iç yapısını ve üst zaman dilimini teyit et",
    description: "Botun düzeltmesinde iki kontrol eklendi: ilk dalganın gerçekten çok bacaklı (beşli) olduğunun doğrulanması ve üst zaman diliminde trendin teyit edilmesi.",
    source: HB,
    check: (c) => info(`${c.variant.firstWave || 'İlk dalganın'} iç yapısını alt zaman diliminde, trend yönünü bir üst zaman diliminde teyit et.`),
  },
  {
    id: 'HB-3', type: 'reminder', patterns: ['*'], sets: ALL, need: 3, phase: 'post',
    name: 'Önce geçerli sayım, sonra oran',
    description: 'Kırılmaz bir kural bozulduysa Fibonacci hedefleri hesaplanmaz. Geçersiz bir sayıma oran uygulamak anlamsızdır (EWP, Handbook ve kurs aynı görüşte).',
    source: `${HB}; EWP Ders 20; Elliott Wave Handbook`,
    check(c) {
      return c.results.some((r) => r.status === 'fail')
        ? info('Kırılmaz kural bozuldu: bu sayım için hedef çizilmedi. Alternatif sayıma geç.')
        : null;
    },
  },
  {
    id: 'HB-4', type: 'reminder', patterns: ['*'], sets: ALL, need: 3,
    name: 'Alternatif sayımı her zaman elde tut',
    description: 'Piyasa ana senaryoyu bozarsa ikinci en iyi yorum hemen yeni ana sayım olmalıdır (EWP Ders 15).',
    source: `EWP Ders 15; ${HB}`,
    check: () => info('Bir alternatif sayım tut: geçersizlik seviyesi kırılırsa hemen ikinci adaya geç.'),
  },
  {
    id: 'HB-5', type: 'reminder', patterns: ['*'], sets: ALL, need: 3,
    name: 'Stop, geçersizlik seviyesine göre konur',
    description: "Botta stop mesafesi dalga 1'in yalnızca %15'iydi ve sabitti; sayım bozulmadan stop çalışıyordu. Düzeltmede bu oran 0,382'ye çıkarıldı. Stop, sayımın geçersizlik seviyesine göre konmalıdır.",
    source: HB,
    check: () => info("Stop'u geçersizlik seviyesine göre düşün; dalga 1'in %15'i gibi dar ve sabit stoplar sayım bozulmadan çalışır."),
  },
  {
    id: 'HB-6', type: 'reminder', patterns: ['*'], sets: ALL, need: 3,
    name: 'Küçük zaman dilimlerinde güvenilirlik düşer',
    description: 'Handbook, WyzFi ve Neely ayrı ayrı uyarıyor: zaman dilimi küçüldükçe gürültü artar ve başarı oranı düşer. İtki yapısı 1 saatlik, 4 saatlik veya günlük grafikte net görünmelidir.',
    source: 'Elliott Wave Handbook; WyzFi s.3; Neely',
    check: () => info('Grafik 1-15 dakikalıksa sayımı 1 saatlik, 4 saatlik veya günlük grafikte de doğrula.'),
  },
  {
    id: 'HB-7', type: 'reminder', patterns: ['*'], sets: ALL, need: 3,
    name: 'Büyük hareketlerde log ölçek',
    description: 'Fiyat aralığı çok genişse (ör. %50\'den fazla) kanal ve oranlar log ölçekte daha anlamlıdır.',
    source: "Elliott Ch.9 & 12; Obsidian notu (log/lineer ölçek)",
    check(c) {
      if (c.logScale) return null;
      const mx = Math.max(...c.p), mn = Math.min(...c.p);
      return mn > 0 && mx / mn > 1.5 ? info(`Fiyat aralığı geniş (en yüksek / en düşük = ${times(mx / mn)}). Log ölçek seçeneğini dene.`) : null;
    },
  },
];
