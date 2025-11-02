import React, { useEffect, useState } from 'react';
import ServiceBadge from './components/ServiceBadge';
import SubscriptionSettings from './components/SubscriptionSettings';

const SERVICES = ['gamepass', 'psplus', 'ubisoftplus'];
const API_BASE_URL = window?.config?.apiBaseUrl ?? 'http://localhost:8000';

export default function App() {
  const [query, setQuery] = useState('');
  const [debouncedQuery, setDebouncedQuery] = useState('');
  const [results, setResults] = useState([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);
  const [subscriptions, setSubscriptions] = useState({
    gamepass: true,
    psplus: true,
    ubisoftplus: true
  });

  useEffect(() => {
    const handler = setTimeout(() => {
      setDebouncedQuery(query.trim());
    }, 350);
    return () => clearTimeout(handler);
  }, [query]);

  useEffect(() => {
    if (!debouncedQuery) {
      setResults([]);
      setError(null);
      return undefined;
    }

    const controller = new AbortController();

    async function runSearch() {
      setLoading(true);
      setError(null);
      try {
        const response = await fetch(
          `${API_BASE_URL}/search?q=${encodeURIComponent(debouncedQuery)}`,
          { signal: controller.signal }
        );
        if (!response.ok) {
          throw new Error(`Search failed (${response.status})`);
        }
        const payload = await response.json();
        setResults(payload.results ?? []);
      } catch (err) {
        if (err.name === 'AbortError') {
          return;
        }
        setError(err.message || 'Unable to search right now.');
        setResults([]);
      } finally {
        setLoading(false);
      }
    }

    runSearch();
    return () => controller.abort();
  }, [debouncedQuery]);

  const hasResults = results.length > 0;

  return (
    <div className="app-container">
      <header className="app-header">
        <div>
          <h1>Game Availability</h1>
          <p className="subtitle">Check Xbox Game Pass, PlayStation Plus, and Ubisoft+ at a glance.</p>
        </div>
        <SubscriptionSettings selected={subscriptions} onChange={setSubscriptions} />
      </header>

      <div className="search-bar">
        <input
          type="search"
          placeholder="Search for a game..."
          value={query}
          onChange={(event) => setQuery(event.target.value)}
        />
      </div>

      {loading && <div className="status">Searching...</div>}
      {error && <div className="status error">{error}</div>}

      {!loading && !error && !hasResults && debouncedQuery && (
        <div className="status">No results found.</div>
      )}

      <div className="results-list">
        {results.map((result) => (
          <div key={result.igdb_id} className="result-card">
            <div className="result-header">
              <div>
                <div className="result-name">{result.name}</div>
                {result.first_release_year && (
                  <div className="result-year">{result.first_release_year}</div>
                )}
              </div>
            </div>
            <div className="service-grid">
              {SERVICES.map((service) => (
                <ServiceBadge
                  key={`${result.igdb_id}-${service}`}
                  service={service}
                  data={result.services?.[service]}
                  enabled={subscriptions[service]}
                />
              ))}
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}
