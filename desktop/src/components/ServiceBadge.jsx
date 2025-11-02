import React from 'react';

const SERVICE_LABELS = {
  gamepass: 'Xbox Game Pass',
  psplus: 'PlayStation Plus',
  ubisoftplus: 'Ubisoft+'
};

function formatPlatforms(platforms = []) {
  if (!platforms || platforms.length === 0) {
    return 'All platforms';
  }
  return platforms.map((p) => p.toUpperCase()).join(', ');
}

export default function ServiceBadge({ service, data, enabled }) {
  const label = SERVICE_LABELS[service] ?? service;
  const isAvailable = Boolean(data?.available);
  const className = [
    'service-badge',
    isAvailable ? 'available' : 'unavailable',
    !enabled ? 'disabled' : ''
  ]
    .filter(Boolean)
    .join(' ');

  return (
    <div className={className}>
      <div className="service-name">{label}</div>
      <div className="service-status">
        {isAvailable ? 'Available' : 'Not available'}
        {isAvailable && data?.tier ? ` (${data.tier})` : ''}
      </div>
      {isAvailable && (
        <div className="service-meta">
          <span className="service-platforms">{formatPlatforms(data?.platforms)}</span>
        </div>
      )}
    </div>
  );
}
