'use client';
import { useState } from 'react';

export default function ForgotPasswordPage() {
  const [email, setEmail] = useState('');
  const [submitted, setSubmitted] = useState(false);
  return <main className="container authPage"><form className="dataCard authForm" onSubmit={(e) => {
    e.preventDefault();
    void fetch('/api/auth/forgot-password', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ email }) }).finally(() => setSubmitted(true));
  }}><h1>Reset password</h1><label className="label">Email</label><input type="email" value={email} onChange={(e)=>setEmail(e.target.value)} required />
  <button type="submit">Send reset link</button>{submitted ? <p className="statusLine">If an account exists, a reset link has been sent.</p> : null}</form></main>;
}
