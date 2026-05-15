import { supabase } from './supabase';

export async function signInWithGoogle() {
  const { data, error } = await supabase.auth.signInWithOAuth({
    provider: 'google',
    options: {
      redirectTo: `${window.location.origin}/auth/callback`,
    },
  });

  if (error) {
    console.error('Auth error:', error);
    throw error;
  }

  return data;
}

export async function checkUserExists(userId: string) {
  const { data, error } = await supabase
    .from('users')
    .select('id')
    .eq('id', userId)
    .single();

  if (error && error.code !== 'PGRST116') {
    console.error('Error checking user:', error);
    throw error;
  }

  return !!data;
}

export async function createUserFromGoogle(googleData: {
  id: string;
  email: string;
  name: string;
  avatar_url?: string;
}) {
  const { data, error } = await supabase
    .from('users')
    .insert({
      id: googleData.id,
      email: googleData.email,
      name: googleData.name,
    })
    .select()
    .single();

  if (error) {
    console.error('Error creating user:', error);
    throw error;
  }

  return data;
}

export async function getCurrentUser() {
  const { data, error } = await supabase.auth.getUser();

  if (error) {
    console.error('Error getting user:', error);
    return null;
  }

  return data.user;
}

export async function signOut() {
  const { error } = await supabase.auth.signOut();

  if (error) {
    console.error('Error signing out:', error);
    throw error;
  }
}
