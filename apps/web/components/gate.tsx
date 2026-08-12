"use client";

import { useEffect, useRef, useState } from "react";

/**
 * The registration gate, ported from `showNewUserGate()` in the legacy client.
 *
 * Three steps behind a blurred navy scrim, dismissible by nothing — no click-outside, no
 * Escape — exactly as before. The sequence is the point: sign in, complete a profile,
 * verify a mobile number, and only then does the chat appear.
 *
 * Two honest differences while Firebase is not yet wired (Phase 6):
 *   - the Google button mints a development identity that is stable per browser, so a
 *     returning visitor lands on the same account;
 *   - the OTP step accepts `123456`, which is the same test code the legacy gate
 *     advertised at the bottom of its own OTP screen.
 * Everything the form collects is real and is persisted through `PATCH /me`.
 */

const GOLD = "#D4AF37";
const PALE = "#F5E6C8";
const NAVY = "#0B2545";
const DEV_OTP = "123456";

interface Country {
  flag: string;
  name: string;
  code: string;
  digits: number;
}

const COUNTRIES: [Country, ...Country[]] = [
  { flag: "🇮🇳", name: "India", code: "+91", digits: 10 },
  { flag: "🇺🇸", name: "United States", code: "+1", digits: 10 },
  { flag: "🇬🇧", name: "United Kingdom", code: "+44", digits: 10 },
  { flag: "🇦🇪", name: "United Arab Emirates", code: "+971", digits: 9 },
  { flag: "🇸🇬", name: "Singapore", code: "+65", digits: 8 },
  { flag: "🇦🇺", name: "Australia", code: "+61", digits: 9 },
  { flag: "🇨🇦", name: "Canada", code: "+1", digits: 10 },
  { flag: "🇩🇪", name: "Germany", code: "+49", digits: 11 },
];

export interface Registration {
  uid: string;
  firstName: string;
  lastName: string;
  preferredName: string;
  gender: string;
  phone: string;
  dateOfBirth: string;
}

const label: React.CSSProperties = {
  fontSize: 9,
  color: "rgba(245,230,200,0.5)",
  letterSpacing: ".8px",
  marginBottom: 4,
};

const field: React.CSSProperties = {
  width: "100%",
  padding: "11px 14px",
  borderRadius: 10,
  border: "1.5px solid rgba(245,230,200,0.15)",
  background: "rgba(245,230,200,0.06)",
  color: PALE,
  fontSize: 13,
  fontFamily: "var(--font-body), sans-serif",
  boxSizing: "border-box",
  outline: "none",
};

const primaryButton: React.CSSProperties = {
  width: "100%",
  padding: 13,
  borderRadius: 12,
  border: "none",
  background: GOLD,
  color: NAVY,
  fontSize: 14,
  fontWeight: 600,
  cursor: "pointer",
  fontFamily: "var(--font-body), sans-serif",
  marginTop: 14,
};

function devUid(): string {
  const key = "bella.dev-uid";
  let uid = localStorage.getItem(key);
  if (!uid) {
    uid = `local_${crypto.randomUUID().slice(0, 8)}`;
    localStorage.setItem(key, uid);
  }
  return uid;
}

export function Gate({
  onComplete,
  busy,
  error,
}: {
  onComplete: (registration: Registration) => void;
  busy: boolean;
  error: string | null;
}) {
  const [step, setStep] = useState<1 | 2 | 3>(1);
  const [uid, setUid] = useState("");
  const [stepError, setStepError] = useState("");

  const [firstName, setFirstName] = useState("");
  const [lastName, setLastName] = useState("");
  const [preferredName, setPreferredName] = useState("");
  const [gender, setGender] = useState("");
  const [country, setCountry] = useState<Country>(COUNTRIES[0]);
  const [countryOpen, setCountryOpen] = useState(false);
  const [countryQuery, setCountryQuery] = useState("");
  const [mobile, setMobile] = useState("");
  const [dd, setDd] = useState("");
  const [mm, setMm] = useState("");
  const [yyyy, setYyyy] = useState("");

  const [otp, setOtp] = useState("");
  const [otpVisible, setOtpVisible] = useState(false);

  const mmRef = useRef<HTMLInputElement>(null);
  const yyyyRef = useRef<HTMLInputElement>(null);

  // Escape must not dismiss the gate — there is nothing behind it to return to.
  useEffect(() => {
    const block = (event: KeyboardEvent) => {
      if (event.key === "Escape") event.preventDefault();
    };
    document.addEventListener("keydown", block);
    return () => document.removeEventListener("keydown", block);
  }, []);

  function startSignIn() {
    setStepError("");
    setUid(devUid());
    setStep(2);
  }

  function submitRegistration() {
    if (!firstName.trim() || !lastName.trim())
      return setStepError("Please enter your name.");
    if (!preferredName.trim()) return setStepError("Please tell Bella what to call you.");
    if (!gender) return setStepError("Please select a gender.");
    if (mobile.length !== country.digits) {
      return setStepError(`A ${country.name} number is ${country.digits} digits.`);
    }
    const day = Number(dd);
    const month = Number(mm);
    const year = Number(yyyy);
    if (
      !day ||
      !month ||
      !year ||
      day > 31 ||
      month > 12 ||
      year > new Date().getFullYear()
    ) {
      return setStepError("Please enter a valid date of birth.");
    }
    setStepError("");
    setStep(3);
  }

  function verify() {
    if (otp !== DEV_OTP) return setStepError("That code is not right. Try 123456.");
    setStepError("");
    onComplete({
      uid,
      firstName: firstName.trim(),
      lastName: lastName.trim(),
      preferredName: preferredName.trim(),
      gender,
      phone: `${country.code}${mobile}`,
      dateOfBirth: `${yyyy}-${mm.padStart(2, "0")}-${dd.padStart(2, "0")}`,
    });
  }

  const message = stepError || error || "";
  const filtered = COUNTRIES.filter((entry) =>
    `${entry.name} ${entry.code}`.toLowerCase().includes(countryQuery.toLowerCase()),
  );

  return (
    <div
      id="bella-gate"
      style={{
        position: "fixed",
        inset: 0,
        background: "rgba(11,37,69,0.85)",
        backdropFilter: "blur(16px)",
        WebkitBackdropFilter: "blur(16px)",
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        zIndex: 150,
        overflowY: "auto",
        padding: 20,
        boxSizing: "border-box",
      }}
    >
      <div
        style={{
          background: NAVY,
          borderRadius: 24,
          width: 420,
          maxWidth: "95vw",
          boxShadow: "0 24px 80px rgba(0,0,0,0.4)",
          border: "1px solid rgba(212,175,55,0.3)",
          overflow: "hidden",
        }}
      >
        <div
          id="gate-main-header"
          style={{
            padding: "28px 28px 20px",
            textAlign: "center",
            borderBottom: "1px solid rgba(212,175,55,0.2)",
          }}
        >
          <div
            style={{
              width: 56,
              height: 56,
              borderRadius: "50%",
              background: GOLD,
              display: "flex",
              alignItems: "center",
              justifyContent: "center",
              margin: "0 auto 12px",
            }}
          >
            <span
              style={{
                fontFamily: "var(--font-display), serif",
                fontSize: 26,
                fontWeight: 600,
                color: NAVY,
              }}
            >
              B
            </span>
          </div>
          <div
            style={{
              fontFamily: "var(--font-display), serif",
              fontSize: 22,
              fontWeight: 600,
              color: PALE,
              marginBottom: 4,
            }}
          >
            Welcome to Bella.ai
          </div>
          <div
            style={{
              fontSize: 11,
              color: "rgba(245,230,200,0.5)",
              letterSpacing: ".5px",
            }}
          >
            create your account to get started
          </div>
        </div>

        {/* ── Step 1 — sign in ─────────────────────────────────────────── */}
        {step === 1 && (
          <div id="gate-step-1" style={{ padding: "24px 28px" }}>
            <button
              onClick={startSignIn}
              style={{
                width: "100%",
                padding: 13,
                borderRadius: 12,
                border: "1px solid rgba(245,230,200,0.2)",
                background: "rgba(245,230,200,0.08)",
                color: PALE,
                fontSize: 14,
                fontWeight: 500,
                cursor: "pointer",
                fontFamily: "var(--font-body), sans-serif",
                display: "flex",
                alignItems: "center",
                justifyContent: "center",
                gap: 10,
              }}
            >
              <svg width="18" height="18" viewBox="0 0 24 24">
                <path
                  fill="#4285F4"
                  d="M22.56 12.25c0-.78-.07-1.53-.2-2.25H12v4.26h5.92c-.26 1.37-1.04 2.53-2.21 3.31v2.77h3.57c2.08-1.92 3.28-4.74 3.28-8.09z"
                />
                <path
                  fill="#34A853"
                  d="M12 23c2.97 0 5.46-.98 7.28-2.66l-3.57-2.77c-.98.66-2.23 1.06-3.71 1.06-2.86 0-5.29-1.93-6.16-4.53H2.18v2.84C3.99 20.53 7.7 23 12 23z"
                />
                <path
                  fill="#FBBC05"
                  d="M5.84 14.09c-.22-.66-.35-1.36-.35-2.09s.13-1.43.35-2.09V7.07H2.18C1.43 8.55 1 10.22 1 12s.43 3.45 1.18 4.93l2.85-2.22.81-.62z"
                />
                <path
                  fill="#EA4335"
                  d="M12 5.38c1.62 0 3.06.56 4.21 1.64l3.15-3.15C17.45 2.09 14.97 1 12 1 7.7 1 3.99 3.47 2.18 7.07l3.66 2.84c.87-2.6 3.3-4.53 6.16-4.53z"
                />
              </svg>
              Continue with Google
            </button>
            <div
              style={{
                fontSize: 11,
                color: "#ff6b6b",
                minHeight: 16,
                marginTop: 8,
                textAlign: "center",
              }}
            >
              {message}
            </div>
            <div
              style={{
                fontSize: 10,
                color: "rgba(245,230,200,0.3)",
                textAlign: "center",
                marginTop: 16,
              }}
            >
              🔒 Your data is private and secure
            </div>
          </div>
        )}

        {/* ── Step 2 — profile ─────────────────────────────────────────── */}
        {step === 2 && (
          <div id="gate-step-2" style={{ padding: "14px 20px 18px" }}>
            <div
              style={{
                textAlign: "center",
                paddingBottom: 12,
                marginBottom: 12,
                borderBottom: "1px solid rgba(212,175,55,0.2)",
              }}
            >
              <div
                style={{
                  fontSize: 12,
                  fontWeight: 600,
                  color: GOLD,
                  letterSpacing: ".5px",
                }}
              >
                PLEASE COMPLETE YOUR PROFILE
              </div>
            </div>

            <div
              id="gate-google-account"
              style={{
                display: "flex",
                alignItems: "center",
                gap: 16,
                padding: 14,
                background: "rgba(212,175,55,0.08)",
                borderRadius: 14,
                marginBottom: 16,
                border: "1px solid rgba(212,175,55,0.2)",
              }}
            >
              <div
                title="Avatar upload arrives with Phase 5"
                style={{
                  width: 100,
                  height: 100,
                  borderRadius: "50%",
                  border: "2px dashed rgba(212,175,55,0.6)",
                  background: "rgba(212,175,55,0.1)",
                  display: "flex",
                  flexDirection: "column",
                  alignItems: "center",
                  justifyContent: "center",
                  gap: 4,
                  flexShrink: 0,
                }}
              >
                <svg
                  width="32"
                  height="32"
                  viewBox="0 0 24 24"
                  fill="none"
                  stroke="rgba(212,175,55,0.6)"
                  strokeWidth="1.5"
                >
                  <path d="M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2" />
                  <circle cx="12" cy="7" r="4" />
                </svg>
                <span
                  style={{
                    fontSize: 9,
                    color: "rgba(212,175,55,0.6)",
                    letterSpacing: ".5px",
                  }}
                >
                  ADD PHOTO
                </span>
              </div>

              <div style={{ flex: 1, minWidth: 0 }}>
                <div
                  style={{
                    fontSize: 14,
                    color: PALE,
                    fontWeight: 600,
                    marginBottom: 4,
                    overflow: "hidden",
                    textOverflow: "ellipsis",
                    whiteSpace: "nowrap",
                  }}
                >
                  {preferredName || firstName || "New account"}
                </div>
                <div
                  style={{
                    fontSize: 11,
                    color: "rgba(245,230,200,0.5)",
                    marginBottom: 8,
                    overflow: "hidden",
                    textOverflow: "ellipsis",
                    whiteSpace: "nowrap",
                  }}
                >
                  {uid}@localhost
                </div>
                <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
                  <svg
                    width="14"
                    height="14"
                    viewBox="0 0 24 24"
                    fill="none"
                    stroke={GOLD}
                    strokeWidth="2.5"
                  >
                    <polyline points="20 6 9 17 4 12" />
                  </svg>
                  <span style={{ fontSize: 10, color: GOLD, letterSpacing: ".3px" }}>
                    Development sign-in
                  </span>
                </div>
              </div>
            </div>

            <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
              <div style={{ display: "flex", gap: 10 }}>
                <div style={{ flex: 1 }}>
                  <div style={label}>FIRST NAME *</div>
                  <input
                    style={field}
                    placeholder="First name"
                    value={firstName}
                    onChange={(event) => setFirstName(event.target.value)}
                  />
                </div>
                <div style={{ flex: 1 }}>
                  <div style={label}>LAST NAME *</div>
                  <input
                    style={field}
                    placeholder="Last name"
                    value={lastName}
                    onChange={(event) => setLastName(event.target.value)}
                  />
                </div>
              </div>

              <div style={{ display: "flex", gap: 10 }}>
                <div style={{ flex: 1 }}>
                  <div style={label}>ADDRESSED AS *</div>
                  <div
                    style={{
                      fontSize: 9,
                      color: "rgba(245,230,200,0.35)",
                      marginBottom: 4,
                    }}
                  >
                    What should Bella call you?
                  </div>
                  <input
                    style={field}
                    placeholder="e.g. Savio, Joy, Neil…"
                    value={preferredName}
                    onChange={(event) => setPreferredName(event.target.value)}
                  />
                </div>
                <div style={{ flex: 1 }}>
                  <div style={label}>GENDER *</div>
                  <div style={{ fontSize: 9, marginBottom: 4 }}>&nbsp;</div>
                  <select
                    style={field}
                    value={gender}
                    onChange={(event) => setGender(event.target.value)}
                  >
                    <option value="" disabled>
                      Select
                    </option>
                    <option value="male">Male</option>
                    <option value="female">Female</option>
                    <option value="non_binary">Non-binary</option>
                    <option value="prefer_not">Prefer not to say</option>
                  </select>
                </div>
              </div>

              <div>
                <div style={label}>MOBILE NUMBER *</div>
                <div style={{ display: "flex", gap: 8, position: "relative" }}>
                  <div style={{ position: "relative", flexShrink: 0 }}>
                    <div
                      onClick={() => setCountryOpen((open) => !open)}
                      style={{
                        ...field,
                        width: 150,
                        cursor: "pointer",
                        display: "flex",
                        alignItems: "center",
                        justifyContent: "space-between",
                        gap: 6,
                      }}
                    >
                      <span>
                        {country.flag} {country.name} {country.code}
                      </span>
                      <svg
                        width="10"
                        height="10"
                        viewBox="0 0 24 24"
                        fill="none"
                        stroke="rgba(245,230,200,0.5)"
                        strokeWidth="2"
                      >
                        <polyline points="6 9 12 15 18 9" />
                      </svg>
                    </div>

                    {countryOpen && (
                      <div
                        style={{
                          position: "absolute",
                          top: "calc(100% + 4px)",
                          left: 0,
                          width: 240,
                          background: NAVY,
                          border: "1px solid rgba(212,175,55,0.3)",
                          borderRadius: 10,
                          zIndex: 20,
                          maxHeight: 220,
                          overflowY: "auto",
                        }}
                      >
                        <input
                          autoFocus
                          style={{ ...field, borderRadius: 0, border: "none" }}
                          placeholder="🔍 Search country…"
                          value={countryQuery}
                          onChange={(event) => setCountryQuery(event.target.value)}
                        />
                        {filtered.map((entry) => (
                          <div
                            key={`${entry.name}${entry.code}`}
                            onClick={() => {
                              setCountry(entry);
                              setMobile("");
                              setCountryOpen(false);
                              setCountryQuery("");
                            }}
                            style={{
                              padding: "9px 12px",
                              fontSize: 12,
                              color: PALE,
                              cursor: "pointer",
                            }}
                          >
                            {entry.flag} {entry.name} {entry.code}
                          </div>
                        ))}
                      </div>
                    )}
                  </div>

                  <input
                    style={{ ...field, flex: 1 }}
                    placeholder={"X".repeat(country.digits)}
                    inputMode="numeric"
                    maxLength={country.digits}
                    value={mobile}
                    onChange={(event) => setMobile(event.target.value.replace(/\D/g, ""))}
                  />
                </div>
              </div>

              <div>
                <div style={label}>DATE OF BIRTH *</div>
                <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
                  <input
                    style={{ ...field, width: 60, textAlign: "center" }}
                    placeholder="DD"
                    maxLength={2}
                    inputMode="numeric"
                    value={dd}
                    onChange={(event) => {
                      const value = event.target.value.replace(/\D/g, "");
                      setDd(value);
                      if (value.length === 2) mmRef.current?.focus();
                    }}
                  />
                  <span style={{ color: "rgba(245,230,200,0.4)" }}>/</span>
                  <input
                    ref={mmRef}
                    style={{ ...field, width: 60, textAlign: "center" }}
                    placeholder="MM"
                    maxLength={2}
                    inputMode="numeric"
                    value={mm}
                    onChange={(event) => {
                      const value = event.target.value.replace(/\D/g, "");
                      setMm(value);
                      if (value.length === 2) yyyyRef.current?.focus();
                    }}
                  />
                  <span style={{ color: "rgba(245,230,200,0.4)" }}>/</span>
                  <input
                    ref={yyyyRef}
                    style={{ ...field, width: 78, textAlign: "center" }}
                    placeholder="YYYY"
                    maxLength={4}
                    inputMode="numeric"
                    value={yyyy}
                    onChange={(event) => setYyyy(event.target.value.replace(/\D/g, ""))}
                  />
                </div>
              </div>
            </div>

            <div
              style={{ fontSize: 11, color: "#ff6b6b", minHeight: 16, marginTop: 8 }}
              role="alert"
            >
              {message}
            </div>

            <button onClick={submitRegistration} style={primaryButton}>
              Verify Mobile &amp; Continue →
            </button>

            <div
              style={{
                fontSize: 9,
                color: "rgba(245,230,200,0.3)",
                textAlign: "center",
                marginTop: 10,
              }}
            >
              * All fields are required · 🔒 Private &amp; secure
            </div>
          </div>
        )}

        {/* ── Step 3 — OTP ─────────────────────────────────────────────── */}
        {step === 3 && (
          <div id="gate-step-3" style={{ padding: "24px 28px" }}>
            <div
              style={{
                fontSize: 12,
                fontWeight: 600,
                color: GOLD,
                letterSpacing: ".5px",
                textAlign: "center",
              }}
            >
              VERIFY YOUR MOBILE
            </div>
            <div
              style={{
                fontSize: 11,
                color: "rgba(245,230,200,0.5)",
                textAlign: "center",
                margin: "8px 0 16px",
              }}
            >
              We sent a code to {country.code} {mobile}
            </div>

            <div style={{ display: "flex", gap: 8 }}>
              <input
                autoFocus
                style={{ ...field, flex: 1, letterSpacing: 4, textAlign: "center" }}
                type={otpVisible ? "text" : "password"}
                placeholder="Enter OTP"
                maxLength={6}
                inputMode="numeric"
                value={otp}
                onChange={(event) => setOtp(event.target.value.replace(/\D/g, ""))}
                onKeyDown={(event) => event.key === "Enter" && verify()}
              />
              <button
                onClick={() => setOtpVisible((visible) => !visible)}
                title="Show/Hide OTP"
                style={{
                  ...field,
                  width: 46,
                  cursor: "pointer",
                  display: "flex",
                  alignItems: "center",
                  justifyContent: "center",
                }}
              >
                👁
              </button>
            </div>

            <div
              style={{ fontSize: 11, color: "#ff6b6b", minHeight: 16, marginTop: 8 }}
              role="alert"
            >
              {message}
            </div>

            <button onClick={verify} disabled={busy} style={primaryButton}>
              {busy ? "Entering…" : "Verify & Enter Bella"}
            </button>

            <button
              onClick={() => setStep(2)}
              style={{
                width: "100%",
                marginTop: 8,
                padding: 10,
                background: "none",
                border: "none",
                color: "rgba(245,230,200,0.5)",
                fontSize: 11,
                cursor: "pointer",
                fontFamily: "var(--font-body), sans-serif",
              }}
            >
              ← Change details
            </button>

            <div
              style={{
                fontSize: 10,
                color: "rgba(245,230,200,0.35)",
                textAlign: "center",
                marginTop: 12,
                lineHeight: 1.6,
              }}
            >
              Test users: enter <strong style={{ color: GOLD }}>{DEV_OTP}</strong> if you
              are registered as a test number
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
