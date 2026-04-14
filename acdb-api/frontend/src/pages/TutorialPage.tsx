import { Link } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { TUTORIAL_WORKFLOWS } from './tutorialWorkflows';

function helpHref(sectionId: string | null): string | null {
  if (!sectionId) return null;
  return `/help#${encodeURIComponent(sectionId)}`;
}

export default function TutorialPage() {
  const { t } = useTranslation('tutorial');

  return (
    <div className="max-w-4xl mx-auto">
      <div className="mb-8">
        <h1 className="text-2xl font-bold text-gray-800">{t('title')}</h1>
        <p className="text-sm text-gray-500 mt-1">{t('subtitle')}</p>
        <p className="text-sm text-gray-700 leading-relaxed mt-4">{t('intro')}</p>
      </div>

      <div className="space-y-8 pb-10">
        {TUTORIAL_WORKFLOWS.map((wf) => {
          const title = t(`${wf.i18nKey}.title`);
          const description = t(`${wf.i18nKey}.description`);
          const roleLabel = t(`roles.${wf.rolesKey}`);
          const steps = t(`${wf.i18nKey}.steps`, { returnObjects: true }) as string[];

          return (
            <section
              key={wf.id}
              id={wf.id}
              className="bg-white rounded-xl border border-gray-200 p-5 sm:p-6 scroll-mt-20"
            >
              <div className="flex flex-col sm:flex-row sm:items-start sm:justify-between gap-3 mb-4">
                <div>
                  <h2 className="text-lg font-bold text-gray-900">{title}</h2>
                  <p className="text-sm text-gray-600 mt-1">{description}</p>
                </div>
                <div className="shrink-0 text-xs text-gray-500 bg-gray-50 border border-gray-100 rounded-lg px-3 py-2">
                  <span className="font-semibold text-gray-600">{t('typicalRoles')}: </span>
                  {roleLabel}
                </div>
              </div>

              <ol className="list-decimal list-outside ml-4 sm:ml-5 space-y-4 text-sm text-gray-800">
                {steps.map((stepText, i) => {
                  const path = wf.links[i] ?? null;
                  const helpId = wf.helpSectionIds[i] ?? null;
                  const helpLink = helpHref(helpId);

                  return (
                    <li key={i} className="pl-1">
                      <p className="text-gray-800 leading-relaxed">{stepText}</p>
                      {(path || helpLink) && (
                        <div className="flex flex-wrap gap-2 mt-2">
                          {path && (
                            <Link
                              to={path}
                              className="inline-flex items-center gap-1.5 text-xs font-medium text-white bg-blue-600 hover:bg-blue-700 rounded-md px-2.5 py-1.5 transition-colors"
                            >
                              {t('openPage')}
                              <span className="font-mono opacity-90">{path}</span>
                            </Link>
                          )}
                          {helpLink && (
                            <Link
                              to={helpLink}
                              className="inline-flex items-center text-xs font-medium text-blue-700 bg-blue-50 hover:bg-blue-100 border border-blue-200 rounded-md px-2.5 py-1.5 transition-colors"
                            >
                              {t('readInHelp')}
                            </Link>
                          )}
                        </div>
                      )}
                    </li>
                  );
                })}
              </ol>
            </section>
          );
        })}
      </div>
    </div>
  );
}
