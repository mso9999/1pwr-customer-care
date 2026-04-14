import i18n from 'i18next';
import { initReactI18next } from 'react-i18next';

import commonEn from './en/common.json';
import loginEn from './en/login.json';
import dashboardEn from './en/dashboard.json';
import customersEn from './en/customers.json';
import customerDetailEn from './en/customerDetail.json';
import customerDataEn from './en/customerData.json';
import omReportEn from './en/omReport.json';
import ticketsEn from './en/tickets.json';
import financialEn from './en/financial.json';
import checkMeterEn from './en/checkMeter.json';
import metersEn from './en/meters.json';
import newCustomerEn from './en/newCustomer.json';
import commissionEn from './en/commission.json';
import financingEn from './en/financing.json';
import tariffEn from './en/tariff.json';
import syncEn from './en/sync.json';
import mutationsEn from './en/mutations.json';
import accountsEn from './en/accounts.json';
import transactionsEn from './en/transactions.json';
import pipelineEn from './en/pipeline.json';
import helpEn from './en/help.json';
import tutorialEn from './en/tutorial.json';
import adminEn from './en/admin.json';
import exportEn from './en/export.json';
import tablesEn from './en/tables.json';
import assignMeterEn from './en/assignMeter.json';
import recordPaymentEn from './en/recordPayment.json';
import paymentVerificationEn from './en/paymentVerification.json';
import customerDashboardEn from './en/customerDashboard.json';
import myProfileEn from './en/myProfile.json';

import commonFr from './fr/common.json';
import loginFr from './fr/login.json';
import dashboardFr from './fr/dashboard.json';
import customersFr from './fr/customers.json';
import customerDetailFr from './fr/customerDetail.json';
import customerDataFr from './fr/customerData.json';
import omReportFr from './fr/omReport.json';
import ticketsFr from './fr/tickets.json';
import financialFr from './fr/financial.json';
import checkMeterFr from './fr/checkMeter.json';
import metersFr from './fr/meters.json';
import newCustomerFr from './fr/newCustomer.json';
import commissionFr from './fr/commission.json';
import financingFr from './fr/financing.json';
import tariffFr from './fr/tariff.json';
import syncFr from './fr/sync.json';
import mutationsFr from './fr/mutations.json';
import accountsFr from './fr/accounts.json';
import transactionsFr from './fr/transactions.json';
import pipelineFr from './fr/pipeline.json';
import helpFr from './fr/help.json';
import tutorialFr from './fr/tutorial.json';
import adminFr from './fr/admin.json';
import exportFr from './fr/export.json';
import tablesFr from './fr/tables.json';
import assignMeterFr from './fr/assignMeter.json';
import recordPaymentFr from './fr/recordPayment.json';
import paymentVerificationFr from './fr/paymentVerification.json';
import customerDashboardFr from './fr/customerDashboard.json';
import myProfileFr from './fr/myProfile.json';

const savedLang = localStorage.getItem('cc_lang');

i18n.use(initReactI18next).init({
  lng: savedLang || 'en',
  fallbackLng: 'en',
  defaultNS: 'common',
  interpolation: { escapeValue: false },
  resources: {
    en: {
      common: commonEn,
      login: loginEn,
      dashboard: dashboardEn,
      customers: customersEn,
      customerDetail: customerDetailEn,
      customerData: customerDataEn,
      omReport: omReportEn,
      tickets: ticketsEn,
      financial: financialEn,
      checkMeter: checkMeterEn,
      meters: metersEn,
      newCustomer: newCustomerEn,
      commission: commissionEn,
      financing: financingEn,
      tariff: tariffEn,
      sync: syncEn,
      mutations: mutationsEn,
      accounts: accountsEn,
      transactions: transactionsEn,
      pipeline: pipelineEn,
      help: helpEn,
      tutorial: tutorialEn,
      admin: adminEn,
      export: exportEn,
      tables: tablesEn,
      assignMeter: assignMeterEn,
      recordPayment: recordPaymentEn,
      paymentVerification: paymentVerificationEn,
      customerDashboard: customerDashboardEn,
      myProfile: myProfileEn,
    },
    fr: {
      common: commonFr,
      login: loginFr,
      dashboard: dashboardFr,
      customers: customersFr,
      customerDetail: customerDetailFr,
      customerData: customerDataFr,
      omReport: omReportFr,
      tickets: ticketsFr,
      financial: financialFr,
      checkMeter: checkMeterFr,
      meters: metersFr,
      newCustomer: newCustomerFr,
      commission: commissionFr,
      financing: financingFr,
      tariff: tariffFr,
      sync: syncFr,
      mutations: mutationsFr,
      accounts: accountsFr,
      transactions: transactionsFr,
      pipeline: pipelineFr,
      help: helpFr,
      tutorial: tutorialFr,
      admin: adminFr,
      export: exportFr,
      tables: tablesFr,
      assignMeter: assignMeterFr,
      recordPayment: recordPaymentFr,
      paymentVerification: paymentVerificationFr,
      customerDashboard: customerDashboardFr,
      myProfile: myProfileFr,
    },
  },
});

i18n.on('languageChanged', (lng) => {
  document.documentElement.lang = lng;
  localStorage.setItem('cc_lang', lng);
});

export default i18n;
