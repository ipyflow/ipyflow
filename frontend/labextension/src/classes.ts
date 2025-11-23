import styles from '../style/index.module.css';

function hyphenToCamel(s: string) {
  return s.replace(/-([a-z])/g, (_, c) => c.toUpperCase());
}

function makeRawClassMap<T extends Record<string, string>>(styles: T): T {
  const out: Record<string, string> = {};
  for (const key of Object.keys(styles)) {
    const camel = hyphenToCamel(key);
    out[camel] = key; // value = original hyphenated class name
  }
  return out as Record<string, string> as T;
}

export default makeRawClassMap(styles);
