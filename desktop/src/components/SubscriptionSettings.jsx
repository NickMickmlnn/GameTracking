import React from 'react';

const SERVICE_LABELS = {
  gamepass: 'Xbox Game Pass',
  psplus: 'PlayStation Plus',
  ubisoftplus: 'Ubisoft+'
};

export default function SubscriptionSettings({ selected, onChange }) {
  const handleToggle = (service) => (event) => {
    onChange({
      ...selected,
      [service]: event.target.checked
    });
  };

  return (
    <div className="settings-panel">
      <div className="settings-title">Subscriptions</div>
      {Object.entries(SERVICE_LABELS).map(([service, label]) => (
        <label key={service} className="settings-option">
          <input
            type="checkbox"
            checked={Boolean(selected[service])}
            onChange={handleToggle(service)}
          />
          <span>{label}</span>
        </label>
      ))}
    </div>
  );
}
