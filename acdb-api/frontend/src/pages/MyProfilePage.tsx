import { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { getMyProfile } from '../lib/api';

export default function MyProfilePage() {
  const { t } = useTranslation(['myProfile', 'common']);
  const [customer, setCustomer] = useState<Record<string, unknown> | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');

  useEffect(() => {
    getMyProfile()
      .then(d => setCustomer(d.customer))
      .catch(e => setError(e.message))
      .finally(() => setLoading(false));
  }, []);

  if (loading) return <div className="text-center py-16 text-gray-400">{t('myProfile:loading')}</div>;
  if (error) return <div className="text-center py-16 text-red-500">{error}</div>;
  if (!customer) return <div className="text-center py-16 text-gray-400">{t('myProfile:noProfile')}</div>;

  const name = [customer.first_name, customer.last_name].filter(Boolean).join(' ');

  return (
    <div className="max-w-2xl mx-auto space-y-4 sm:space-y-6">
      <h1 className="text-xl sm:text-2xl font-bold text-gray-800">{t('myProfile:title')}</h1>

      <div className="bg-white rounded-lg shadow p-4 sm:p-6">
        <div className="flex items-center gap-3 sm:gap-4 mb-4 sm:mb-6">
          <div className="w-12 h-12 sm:w-16 sm:h-16 bg-blue-100 rounded-full flex items-center justify-center text-xl sm:text-2xl font-bold text-blue-700 shrink-0">
            {(String(customer.first_name || '')[0] || '?').toUpperCase()}
          </div>
          <div>
            <h2 className="text-xl font-semibold">{name || t('myProfile:fallbackName')}</h2>
            <p className="text-gray-500 text-sm">{String(customer.account_number || customer.customer_id_legacy || '')}</p>
          </div>
        </div>

        <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
          {[
            [t('myProfile:fields.account'), customer.account_number || customer.customer_id_legacy],
            [t('myProfile:fields.firstName'), customer.first_name],
            [t('myProfile:fields.middleName'), customer.middle_name],
            [t('myProfile:fields.lastName'), customer.last_name],
            [t('myProfile:fields.phone'), customer.phone],
            [t('myProfile:fields.cellPhone1'), customer.cell_phone_1],
            [t('myProfile:fields.cellPhone2'), customer.cell_phone_2],
            [t('myProfile:fields.email'), customer.email],
            [t('myProfile:fields.plotNumber'), customer.plot_number],
            [t('myProfile:fields.streetAddress'), customer.street_address],
            [t('myProfile:fields.city'), customer.city],
            [t('myProfile:fields.district'), customer.district],
            [t('myProfile:fields.site'), customer.concession],
            [t('myProfile:fields.dateConnected'), customer.date_connected],
            [t('myProfile:fields.dateTerminated'), customer.date_terminated],
          ].map(([label, value]) => (
            <div key={String(label)}>
              <dt className="text-xs font-medium text-gray-500 uppercase">{String(label)}</dt>
              <dd className="text-sm text-gray-800 mt-0.5">{value ? String(value) : <span className="text-gray-300">--</span>}</dd>
            </div>
          ))}
        </div>

        {/* Account numbers */}
        {Array.isArray(customer.account_numbers) && customer.account_numbers.length > 0 && (
          <div className="mt-6 pt-4 border-t">
            <h3 className="text-sm font-medium text-gray-600 mb-2">{t('myProfile:accountNumbers')}</h3>
            <div className="flex gap-2 flex-wrap">
              {(customer.account_numbers as string[]).map(a => (
                <span key={a} className="px-3 py-1 bg-gray-100 rounded-full text-sm">{a}</span>
              ))}
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
