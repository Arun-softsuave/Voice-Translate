import { useCallback, useEffect, useRef, useState } from 'react'

/**
 * Data for a screen: {data, error, loading, reload}.
 *
 * `load` runs again whenever `deps` change. An answer that arrives after a
 * newer request was made is dropped, so fast filter changes never show stale
 * rows.
 */
export function useLoad(load, deps) {
  const [state, setState] = useState({ data: null, error: null, loading: true })
  const latest = useRef(0)

  const run = useCallback(() => {
    const mine = ++latest.current
    setState((s) => ({ ...s, loading: true, error: null }))
    load()
      .then((data) => {
        if (mine === latest.current) setState({ data, error: null, loading: false })
      })
      .catch((error) => {
        if (mine === latest.current) setState((s) => ({ ...s, error, loading: false }))
      })
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps)

  useEffect(() => {
    run()
  }, [run])

  return { ...state, reload: run }
}
