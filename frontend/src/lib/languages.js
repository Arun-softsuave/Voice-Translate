// Must stay in sync with SUPPORTED_LANGUAGES in backend/app/schemas/call.py.
// The backend validates against its own allowlist, so a mismatch here fails
// closed with a 422 rather than reaching the translation prompt.
export const LANGUAGES = [
  { code: 'ta', label: 'Tamil',     native: 'தமிழ்' },
  { code: 'hi', label: 'Hindi',     native: 'हिन्दी' },
  { code: 'en', label: 'English',   native: 'English' },
  { code: 'te', label: 'Telugu',    native: 'తెలుగు' },
  { code: 'kn', label: 'Kannada',   native: 'ಕನ್ನಡ' },
  { code: 'ml', label: 'Malayalam', native: 'മലയാളം' },
  { code: 'mr', label: 'Marathi',   native: 'मराठी' },
  { code: 'bn', label: 'Bengali',   native: 'বাংলা' },
  { code: 'gu', label: 'Gujarati',  native: 'ગુજરાતી' },
]

export const labelOf = (code) =>
  LANGUAGES.find((l) => l.code === code)?.label ?? code
