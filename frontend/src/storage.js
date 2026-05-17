import { useEffect, useState } from 'react';

export function useLocalStorage(key, initialValue) {
  const [value, setValue] = useState(() => {
    try {
      const raw = localStorage.getItem(key);
      return raw ? JSON.parse(raw) : initialValue;
    } catch {
      return initialValue;
    }
  });

  useEffect(() => {
    const timeout = setTimeout(() => {
      localStorage.setItem(key, JSON.stringify(value));
    }, 300);
    return () => clearTimeout(timeout);
  }, [key, value]);

  return [value, setValue];
}
