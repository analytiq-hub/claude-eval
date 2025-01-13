'use client'

import { Fragment } from 'react';
import { Menu, Transition } from '@headlessui/react';
import { ChevronDownIcon } from '@heroicons/react/20/solid';
import { useOrganization } from '@/contexts/OrganizationContext';

const typeLabels = {
  individual: { bg: 'bg-gray-100', text: 'text-gray-600' },
  team: { bg: 'bg-blue-100', text: 'text-blue-600' },
  enterprise: { bg: 'bg-purple-100', text: 'text-purple-600' }
};

export default function OrganizationSwitcher() {
  const { currentOrganization, switchOrganization, organizations, isLoading } = useOrganization();

  if (isLoading) {
    return <div className="text-white">Loading organizations...</div>;
  }

  if (!currentOrganization) {
    return <div className="text-white">No organization selected</div>;
  }

  // console.log(`organizations: ${JSON.stringify(organizations, null, 2)}`);
  // console.log(`currentOrganization: ${JSON.stringify(currentOrganization, null, 2)}`);
  // console.log(`organization type: ${currentOrganization.type}`);

  // If user has only one organization, show it as text
  if (organizations.length === 1) {
    return (
      <div className="text-gray-200 text-base font-medium px-3 py-2">
        {currentOrganization.name}
      </div>
    );
  }

  // For multiple organizations, show dropdown
  return (
    <Menu as="div" className="relative inline-block text-left">
      <div>
        <Menu.Button className="inline-flex w-full justify-center gap-x-1.5 rounded-md bg-white px-3 py-2 text-sm font-semibold text-gray-900 shadow-sm ring-1 ring-inset ring-gray-300 hover:bg-gray-50">
          {currentOrganization.name}
          <ChevronDownIcon className="-mr-1 h-5 w-5 text-gray-400" aria-hidden="true" />
        </Menu.Button>
      </div>

      <Transition
        as={Fragment}
        enter="transition ease-out duration-100"
        enterFrom="transform opacity-0 scale-95"
        enterTo="transform opacity-100 scale-100"
        leave="transition ease-in duration-75"
        leaveFrom="transform opacity-100 scale-100"
        leaveTo="transform opacity-0 scale-95"
      >
        <Menu.Items className="absolute right-0 z-10 mt-2 w-56 origin-top-right rounded-md bg-white shadow-lg ring-1 ring-black ring-opacity-5 focus:outline-none">
          <div className="py-1">
            {organizations.map((organization) => (
              <Menu.Item key={organization.id}>
                {({ active }) => (
                  <button
                    onClick={() => switchOrganization(organization.id)}
                    className={`
                      ${active ? 'bg-gray-100 text-gray-900' : 'text-gray-700'}
                      block px-4 py-2 text-sm w-full text-left flex items-center justify-between
                    `}
                  >
                    <span>{organization.name}</span>
                    <span className={`text-xs ml-2 px-2 py-1 rounded-full ${typeLabels[organization.type].bg} ${typeLabels[organization.type].text}`}>
                      {organization.type}
                    </span>
                  </button>
                )}
              </Menu.Item>
            ))}
          </div>
        </Menu.Items>
      </Transition>
    </Menu>
  );
}