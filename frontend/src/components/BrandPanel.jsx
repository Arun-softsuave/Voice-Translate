import { useEffect, useState } from 'react'

/**
 * The left half of the sign-in, request-access and setup screens: who we are,
 * what the product does, and a picture of it doing it.
 *
 * Pure decoration around real content. It never shows call state: the two
 * language nodes are the languages chosen (or on offer), not people on a
 * call, and nothing here suggests a call is live.
 */
export function BrandPanel({ eyebrow, heading, lede, pair, catalogue = [], languageCount }) {
  // No pair given (sign-in, request access): show many languages in turn
  // rather than single out one combination.
  const greeting = useGreetingPair(catalogue, !pair)
  const shown = pair ?? greeting
  return (
    <div className="brand setup__brand enter">
      <header className="brand__head">
        <Mark />
        <span className="brand__name">
          Interpreter
          <small>Real-time voice translation</small>
        </span>
      </header>

      <div className="brand__copy">
        {eyebrow && <p className="eyebrow">{eyebrow}</p>}
        <h1 className="display brand__title">{heading}</h1>
        <p className="lede brand__lede">{lede}</p>
      </div>

      <LanguageBridge left={shown[0]} right={shown[1]} turn={pair ? null : shown.turn} />

      <ul className="brand__features">
        <Feature icon={<IconClock />} title="Real-time">
          Translation while you talk, not after the call.
        </Feature>
        <Feature icon={<IconSwap />} title="Two-way">
          Each person speaks and hears their own language.
        </Feature>
        <Feature icon={<IconGlobe />} title="Multilingual">
          {languageCount
            ? `${languageCount} languages to choose from.`
            : 'Choose the language each side speaks.'}
        </Feature>
      </ul>

      <footer className="brand__foot">
        <span className="brand__bars" aria-hidden="true">
          <i /><i /><i /><i /><i />
        </span>
        Interpreter · Real-time communication
      </footer>
    </div>
  )
}

// "Hello" in languages the app offers, ordered so that each pair shown
// together mixes scripts and regions.
const GREETINGS = [
  ['en', 'Hello', 'English'], ['ta', 'வணக்கம்', 'Tamil'],
  ['es', 'Hola', 'Spanish'], ['hi', 'नमस्ते', 'Hindi'],
  ['fr', 'Bonjour', 'French'], ['ja', 'こんにちは', 'Japanese'],
  ['ar', 'مرحبا', 'Arabic'], ['de', 'Hallo', 'German'],
  ['ko', '안녕하세요', 'Korean'], ['ml', 'നമസ്കാരം', 'Malayalam'],
  ['pt', 'Olá', 'Portuguese'], ['te', 'నమస్కారం', 'Telugu'],
  ['zh', '你好', 'Chinese'], ['bn', 'নমস্কার', 'Bengali'],
  ['it', 'Ciao', 'Italian'], ['kn', 'ನಮಸ್ಕಾರ', 'Kannada'],
  ['ru', 'Привет', 'Russian'], ['gu', 'નમસ્તે', 'Gujarati'],
  ['tr', 'Merhaba', 'Turkish'], ['mr', 'नमस्कार', 'Marathi'],
]
const GREETING_MS = 3600

/** Greeting pairs the backend actually offers, as [{native, label, lang}, ...]. */
export function greetingPairs(catalogue) {
  const offered = new Set(catalogue.map((l) => l.code))
  const words = GREETINGS.filter(([code]) => !offered.size || offered.has(code))
    .map(([lang, native, label]) => ({ lang, native, label }))
  const pairs = []
  for (let i = 0; i + 1 < words.length; i += 2) pairs.push([words[i], words[i + 1]])
  return pairs
}

/** One greeting pair, moving to the next every few seconds; still if the
 *  reader prefers reduced motion or the pair is not wanted. */
function useGreetingPair(catalogue, active) {
  const pairs = greetingPairs(catalogue)
  const still = usePrefersReducedMotion()
  const [turn, setTurn] = useState(0)
  useEffect(() => {
    if (!active || still || pairs.length < 2) return undefined
    const id = setInterval(() => setTurn((t) => t + 1), GREETING_MS)
    return () => clearInterval(id)
  }, [active, still, pairs.length])
  const pair = pairs.length ? pairs[turn % pairs.length]
    : [{ native: 'Hello', label: 'English' }, { native: 'Hola', label: 'Spanish' }]
  return Object.assign([...pair], { turn })
}

/**
 * Two languages to show on the bridge, as {native, label}: the user's own
 * choice when there is one, else Tamil and Hindi if offered, else the first
 * two the backend lists.
 */
export function pickPair(catalogue, source, target) {
  const find = (code) => catalogue.find((l) => l.code === code)
  const a = find(source) ?? find('ta') ?? catalogue[0]
  const b = find(target) ?? catalogue.find((l) => l.code === 'hi' && l !== a) ??
    catalogue.find((l) => l !== a)
  const show = (l, fallback) => (l ? { native: l.native, label: l.label }
    : { native: fallback, label: fallback })
  return [show(a, 'Tamil'), show(b, 'Hindi')]
}

function Feature({ icon, title, children }) {
  return (
    <li className="feature">
      <span className="feature__icon" aria-hidden="true">{icon}</span>
      <span className="feature__text">
        <strong>{title}</strong>
        <span>{children}</span>
      </span>
    </li>
  )
}

function usePrefersReducedMotion() {
  const query = '(prefers-reduced-motion: reduce)'
  const [reduced, setReduced] = useState(
    () => typeof window !== 'undefined' && window.matchMedia?.(query).matches)
  useEffect(() => {
    const media = window.matchMedia?.(query)
    if (!media) return undefined
    const update = () => setReduced(media.matches)
    media.addEventListener('change', update)
    return () => media.removeEventListener('change', update)
  }, [])
  return reduced
}

// Upper arc carries the left language across, lower arc brings the answer back.
const OUT = 'M 118 150 C 200 58, 360 58, 442 150'
const BACK = 'M 442 150 C 360 242, 200 242, 118 150'

/**
 * Two languages joined across a faint globe: words leave one side along the
 * upper arc and the reply returns along the lower one. SVG and CSS only.
 */
export function LanguageBridge({ left, right, turn = null }) {
  const still = usePrefersReducedMotion()
  const particles = (path, begin) => (still ? null : [0, 1.2, 2.4].map((offset) => (
    // Invisible until its animation begins, or it waits at the corner.
    <circle key={offset} r="2.6" opacity="0" className="bridge-svg__particle">
      <animateMotion dur="3.6s" begin={`${begin + offset}s`} repeatCount="indefinite"
                     path={path} keyPoints="0;1" keyTimes="0;1" calcMode="linear" />
      <animate attributeName="opacity" dur="3.6s" begin={`${begin + offset}s`}
               repeatCount="indefinite" values="0;1;1;0" keyTimes="0;0.15;0.85;1" />
    </circle>
  )))

  return (
    <figure className="bridge-visual" aria-label={turn !== null
      ? 'Greetings in many languages, translated both ways'
      : `${left.label} and ${right.label}, translated both ways`}>
      <svg viewBox="0 0 560 300" className="bridge-svg" role="presentation"
           preserveAspectRatio="xMidYMid meet">
        <defs>
          <radialGradient id="bridge-glow" cx="50%" cy="50%" r="50%">
            <stop offset="0%" stopColor="#C9A227" stopOpacity="0.16" />
            <stop offset="100%" stopColor="#C9A227" stopOpacity="0" />
          </radialGradient>
          <linearGradient id="bridge-line" x1="0" x2="1">
            <stop offset="0%" stopColor="#E3C566" stopOpacity="0.15" />
            <stop offset="50%" stopColor="#E3C566" stopOpacity="0.75" />
            <stop offset="100%" stopColor="#E3C566" stopOpacity="0.15" />
          </linearGradient>
        </defs>

        {/* the globe: a meridian net behind everything */}
        <g className="bridge-svg__globe">
          <circle cx="280" cy="150" r="128" fill="url(#bridge-glow)" />
          <circle cx="280" cy="150" r="128" />
          <ellipse cx="280" cy="150" rx="86" ry="128" />
          <ellipse cx="280" cy="150" rx="40" ry="128" />
          <ellipse cx="280" cy="150" rx="128" ry="44" />
          <line x1="152" y1="150" x2="408" y2="150" />
          <line x1="280" y1="22" x2="280" y2="278" />
          <circle cx="280" cy="150" r="146" className="bridge-svg__orbit" />
        </g>

        {/* the two directions */}
        <path d={OUT} className="bridge-svg__arc" />
        <path d={BACK} className="bridge-svg__arc" />
        <path d={OUT} className="bridge-svg__flow" />
        <path d={BACK} className="bridge-svg__flow bridge-svg__flow--back" />
        {particles(OUT, 0)}
        {particles(BACK, 1.8)}

        {/* the exchange in the middle */}
        <circle cx="280" cy="150" r="30" className="bridge-svg__pulse" />
        <circle cx="280" cy="150" r="22" className="bridge-svg__hub" />
        <path d="M 270 145 h 20 m -5 -5 l 5 5 l -5 5 M 290 157 h -20 m 5 -5 l -5 5 l 5 5"
              className="bridge-svg__swap" />

        <Node x={86} />
        <Node x={474} delay />
      </svg>

      {/* Greetings change every few seconds; the key restarts the fade-in.
          Hidden from screen readers then, so it is not read out repeatedly. */}
      <figcaption className="bridge-visual__labels" aria-hidden={turn !== null || undefined}>
        <span key={`l${turn}`}
              className={`bridge-visual__label bridge-visual__label--left${turn !== null ? ' is-fresh' : ''}`}>
          <b lang={left.lang} dir="auto">{left.native}</b>{left.label}
        </span>
        <span key={`r${turn}`}
              className={`bridge-visual__label bridge-visual__label--right${turn !== null ? ' is-fresh' : ''}`}>
          <b lang={right.lang} dir="auto">{right.native}</b>{right.label}
        </span>
      </figcaption>
    </figure>
  )
}

function Node({ x, delay }) {
  const heights = [10, 22, 32, 18, 26]
  return (
    <g className={`bridge-svg__node${delay ? ' bridge-svg__node--late' : ''}`}>
      <circle cx={x} cy="150" r="40" className="bridge-svg__ring" />
      <circle cx={x} cy="150" r="32" className="bridge-svg__disc" />
      {heights.map((h, i) => (
        <rect key={i} x={x - 15 + i * 7} y={150 - h / 2} width="3" height={h} rx="1.5"
              className="bridge-svg__bar" style={{ animationDelay: `${i * 0.13}s` }} />
      ))}
    </g>
  )
}

function Mark() {
  // A globe split by a sound wave: the product in one glyph.
  return (
    <svg className="brand__mark" viewBox="0 0 40 40" aria-hidden="true">
      <circle cx="20" cy="20" r="17" />
      <ellipse cx="20" cy="20" rx="7.5" ry="17" />
      <path d="M 5 20 h 6 l 3 -7 l 4 14 l 4 -16 l 4 12 l 3 -3 h 6" className="brand__wave" />
    </svg>
  )
}

const iconProps = {
  viewBox: '0 0 24 24', fill: 'none', stroke: 'currentColor', strokeWidth: 1.5,
  strokeLinecap: 'round', strokeLinejoin: 'round',
}

function IconClock() {
  return (
    <svg {...iconProps}>
      <circle cx="12" cy="12" r="8.5" />
      <path d="M12 7.5V12l3 2" />
    </svg>
  )
}

function IconSwap() {
  return (
    <svg {...iconProps}>
      <path d="M4 8h14m-3-3 3 3-3 3M20 16H6m3 3-3-3 3-3" />
    </svg>
  )
}

function IconGlobe() {
  return (
    <svg {...iconProps}>
      <circle cx="12" cy="12" r="8.5" />
      <path d="M3.5 12h17M12 3.5c2.4 2.6 2.4 14.4 0 17M12 3.5c-2.4 2.6-2.4 14.4 0 17" />
    </svg>
  )
}
