import { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import QRCode from 'qrcode';
import { useCountry } from '../contexts/CountryContext';
import { getWhatsAppBridge, type WhatsAppBridgeStatus } from '../lib/api';

const SOP = 'https://github.com/mso9999/1pwr-customer-care/blob/main/docs/whatsapp-customer-care.md';

const NAMES: Record<string, string> = {
  LS: 'Lesotho',
  BN: 'Benin',
  ZM: 'Zambia',
};

export default function WhatsAppBridgePage() {
  const { t } = useTranslation('whatsappBridge');
  const { country } = useCountry();
  const [status, setStatus] = useState<WhatsAppBridgeStatus | null>(null);
  const [image, setImage] = useState('');
  const [error, setError] = useState('');

  useEffect(() => {
    if (country === 'ALL') return;
    let stop = false;
    let timer = 0;
    const tick = async () => {
      try {
        const next = await getWhatsAppBridge();
        if (stop) return;
        setStatus(next);
        setError('');
        if (next.configured && next.linked === false) {
          timer = window.setTimeout(tick, 8000);
        }
      } catch (err: unknown) {
        if (!stop) setError(err instanceof Error ? err.message : String(err));
      }
    };
    tick();
    return () => {
      stop = true;
      window.clearTimeout(timer);
    };
  }, [country]);

  useEffect(() => {
    if (!status?.qr) {
      setImage('');
      return;
    }
    let cancel = false;
    QRCode.toDataURL(status.qr, { width: 280, margin: 1 }).then((url) => {
      if (!cancel) setImage(url);
    }).catch(() => {
      if (!cancel) setImage('');
    });
    return () => { cancel = true; };
  }, [status?.qr]);

  if (country === 'ALL') {
    return (
      <div className="max-w-3xl mx-auto p-6">
        <h1 className="text-xl font-semibold">{t('title')}</h1>
        <p className="mt-3 text-sm text-gray-600">{t('pickCountry')}</p>
      </div>
    );
  }

  const name = NAMES[country] || country;

  return (
    <div className="max-w-3xl mx-auto p-6 space-y-4">
      <div>
        <h1 className="text-xl font-semibold text-gray-800">{t('title')}</h1>
        <p className="text-sm text-gray-500 mt-1">{t('subtitle')}</p>
      </div>
      {error && <div className="p-3 bg-red-50 border border-red-200 rounded-xl text-sm text-red-700">{error}</div>}
      {!status && !error && <p className="text-sm text-gray-500">{t('loading')}</p>}
      {status && (
        <div className="bg-white border rounded-xl p-5 space-y-4">
          <p className="text-sm"><span className="text-gray-500">{t('country')}: </span><span className="font-medium">{name} ({status.country_code})</span></p>
          {!status.configured && (
            <div className="text-sm text-gray-700 space-y-2">
              <p>{t('notConfigured')}</p>
              <a className="text-blue-700 underline" href={SOP} target="_blank" rel="noreferrer">{t('sop')}</a>
            </div>
          )}
          {status.configured && status.linked === null && (
            <p className="text-sm text-gray-700">{t('unreachable')} {status.detail || ''}</p>
          )}
          {status.configured && status.linked === true && (
            <p className="text-sm font-medium text-green-700">{t('linked')}</p>
          )}
          {status.configured && status.linked === false && (
            <div className="space-y-3">
              <p className="text-sm font-medium text-amber-700">{t('notLinked')}</p>
              <p className="text-sm text-gray-600">{t('steps')}</p>
              {image
                ? <img src={image} alt="WhatsApp QR" className="w-72 h-72 border rounded-lg" />
                : <p className="text-sm text-gray-500">{t('waiting')}</p>}
              {status.pairing_code && (
                <div>
                  <p className="text-sm font-medium">{t('pairing')}</p>
                  <p className="font-mono text-2xl tracking-widest">{status.pairing_code}</p>
                  <p className="text-xs text-gray-500">{t('pairingHelp')}</p>
                </div>
              )}
            </div>
          )}
          <p className="text-sm text-gray-600">
            {t('tracker')}: {status.tracker_group || t('trackerUnset')}
          </p>
        </div>
      )}
    </div>
  );
}
