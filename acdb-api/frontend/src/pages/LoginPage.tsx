import { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { useAuth } from '../contexts/AuthContext';
import { employeeLogin, customerLogin, customerRegister } from '../lib/api';

type Mode = 'employee' | 'customer' | 'register';

export default function LoginPage() {
  const [mode, setMode] = useState<Mode>('employee');
  const [id, setId] = useState('');
  const [password, setPassword] = useState('');
  const [error, setError] = useState('');
  const [success, setSuccess] = useState('');
  const [loading, setLoading] = useState(false);
  const { login } = useAuth();
  const navigate = useNavigate();

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError('');
    setSuccess('');
    setLoading(true);

    try {
      if (mode === 'register') {
        await customerRegister(id, password);
        setSuccess('Registration successful! You can now log in.');
        setMode('customer');
        setPassword('');
      } else if (mode === 'employee') {
        const res = await employeeLogin(id, password);
        login(res.access_token, res.user as any);
        navigate('/dashboard');
      } else {
        const res = await customerLogin(id, password);
        login(res.access_token, res.user as any);
        navigate('/my/dashboard');
      }
    } catch (err: any) {
      setError(err.message || 'Login failed');
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="min-h-screen bg-gradient-to-br from-blue-50 to-blue-100 flex items-center justify-center p-4">
      <div className="w-full max-w-md">
        {/* Header */}
        <div className="text-center mb-8">
          <img src="/1pwr-logo.png" alt="1PWR" className="h-14 w-auto mx-auto mb-3" />
          <h1 className="text-3xl font-bold text-blue-800">Customer Care</h1>
          <p className="text-gray-500 mt-1">Lesotho Minigrid Portal</p>
        </div>

        {/* Card */}
        <div className="bg-white rounded-xl shadow-lg p-8">
          {/* Mode toggle */}
          <div className="flex rounded-lg bg-gray-100 p-1 mb-6">
            <button
              className={`flex-1 py-2 text-sm font-medium rounded-md transition ${mode === 'employee' ? 'bg-white shadow text-blue-700' : 'text-gray-500'}`}
              onClick={() => { setMode('employee'); setError(''); setSuccess(''); }}
            >
              Employee
            </button>
            <button
              className={`flex-1 py-2 text-sm font-medium rounded-md transition ${mode === 'customer' || mode === 'register' ? 'bg-white shadow text-blue-700' : 'text-gray-500'}`}
              onClick={() => { setMode('customer'); setError(''); setSuccess(''); }}
            >
              Customer
            </button>
          </div>

          <form onSubmit={handleSubmit} className="space-y-4">
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">
                {mode === 'employee' ? 'Employee ID' : 'Account Number'}
              </label>
              <input
                type="text"
                value={id}
                onChange={(e) => setId(e.target.value)}
                placeholder={mode === 'employee' ? 'Enter employee ID' : 'e.g. 0045MAK'}
                className="w-full px-3 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-blue-500 focus:border-transparent outline-none"
                required
              />
            </div>

            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">Password</label>
              <input
                type="password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                placeholder={mode === 'employee' ? 'Enter OTP' : mode === 'register' ? 'Choose a password (min 6 chars)' : 'Your password'}
                className="w-full px-3 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-blue-500 focus:border-transparent outline-none"
                required
              />
            </div>

            {error && <p className="text-red-600 text-sm bg-red-50 p-2 rounded">{error}</p>}
            {success && <p className="text-green-700 text-sm bg-green-50 p-2 rounded">{success}</p>}

            <button
              type="submit"
              disabled={loading}
              className="w-full py-2.5 bg-blue-600 text-white rounded-lg font-medium hover:bg-blue-700 disabled:opacity-50 transition"
            >
              {loading ? 'Please wait...' : mode === 'register' ? 'Create Account' : 'Sign In'}
            </button>
          </form>

          {/* Customer register/login toggle */}
          {(mode === 'customer' || mode === 'register') && (
            <p className="text-center text-sm text-gray-500 mt-4">
              {mode === 'customer' ? (
                <>First time? <button onClick={() => setMode('register')} className="text-blue-600 hover:underline">Register here</button></>
              ) : (
                <>Already registered? <button onClick={() => setMode('customer')} className="text-blue-600 hover:underline">Sign in</button></>
              )}
            </p>
          )}
        </div>
      </div>
    </div>
  );
}
